# scraper.py
import asyncio
import logging
import re
import time
import urllib.parse
import warnings
from io import BytesIO
from typing import Optional, List

import aiohttp
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class RbdaScraper:
    # Rate limiting: max 50 requests per minute
    MAX_REQUESTS_PER_MINUTE = 50
    MAX_CONCURRENT_DOWNLOADS = 7  # Max parallel downloads
    
    def __init__(self, login, password):
        self.auth_url = "https://rbda.dc.tj/modules/crud.php?act=auth"
        self.search_url = "https://rbda.dc.tj/pages/searchfines.php"
        self.base_url = "https://rbda.dc.tj"
        self.login = login
        self.password = password
        
        # Connection pooling with retry strategy
        self.session = requests.Session()
        
        # Configure retry strategy
        retry_strategy = Retry(
            total=3,  # Max 3 retries
            backoff_factor=1,  # Wait 1, 2, 4 seconds between retries
            status_forcelist=[429, 500, 502, 503, 504],  # Retry on these status codes
            allowed_methods=["HEAD", "GET", "POST", "PUT", "DELETE", "OPTIONS", "TRACE"]
        )
        
        # Configure connection pooling
        adapter = HTTPAdapter(
            pool_connections=20,  # Pool of 20 connections
            pool_maxsize=40,  # Max 40 connections
            max_retries=retry_strategy
        )
        
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)
        
        # Set timeouts: connect=5s, read=30s
        self.timeout = (5, 30)
        
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Origin": "https://rbda.dc.tj",
            "Referer": "https://rbda.dc.tj/pages/searchfines.php"
        })
        
        self.authenticated = False
        
        # Rate limiting tracking
        self.request_times = []
        self.rate_limit_lock = asyncio.Lock()
        
        # Semaphore for concurrent downloads
        self.download_semaphore = asyncio.Semaphore(self.MAX_CONCURRENT_DOWNLOADS)
        
        # Async HTTP session (created on first use)
        self._aiohttp_session = None

    async def _get_aiohttp_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session for async downloads"""
        if self._aiohttp_session is None or self._aiohttp_session.closed:
            # Configure timeout: 5s connect, 30s total
            timeout = aiohttp.ClientTimeout(total=30, connect=5, sock_read=30)
            
            # Configure connector with connection pooling
            connector = aiohttp.TCPConnector(
                limit=100,  # Max 100 simultaneous connections
                limit_per_host=30,  # Max 30 per host
                ttl_dns_cache=300,  # Cache DNS for 5 minutes
            )
            
            self._aiohttp_session = aiohttp.ClientSession(
                timeout=timeout,
                connector=connector,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                }
            )
        
        return self._aiohttp_session

    async def close_aiohttp_session(self):
        """Close aiohttp session"""
        if self._aiohttp_session and not self._aiohttp_session.closed:
            await self._aiohttp_session.close()
            self._aiohttp_session = None

    async def _rate_limit_check(self):
        """Ensure we don't exceed rate limit"""
        async with self.rate_limit_lock:
            now = time.time()
            # Remove requests older than 1 minute
            self.request_times = [t for t in self.request_times if now - t < 60]
            
            # If we're at the limit, wait
            if len(self.request_times) >= self.MAX_REQUESTS_PER_MINUTE:
                sleep_time = 60 - (now - self.request_times[0])
                if sleep_time > 0:
                    print(f"⏳ Rate limit reached, waiting {sleep_time:.1f} seconds...")
                    await asyncio.sleep(sleep_time)
                    # Clean up again after waiting
                    now = time.time()
                    self.request_times = [t for t in self.request_times if now - t < 60]
            
            # Record this request
            self.request_times.append(time.time())

    def _login(self, force=False):
        if not force and self.authenticated and self.session.cookies.get('PHPSESSID'): 
            return True
        payload = {'login': self.login, 'password': self.password}
        print(f"🚀 Выполняю авторизацию...")
        try:
            response = self.session.post(self.auth_url, data=payload, allow_redirects=True, timeout=self.timeout)
            response.raise_for_status()
            if response.status_code == 200 and 'dashboard.php' in response.url:
                 print("✅ Авторизация прошла успешно!")
                 self.authenticated = True
                 return True
            print("❌ Авторизация не удалась")
            self.authenticated = False
            return False
        except requests.exceptions.RequestException as e:
            print(f"❌ Ошибка при авторизации: {e}")
            self.authenticated = False
            return False
    
    def _check_session_expired(self, response):
        if 'login.php' in response.url or 'auth' in response.url.lower():
            print("⚠️ Сессия истекла, требуется переавторизация (redirect)")
            return True
        
        if '<title>Авторизация</title>' in response.text:
            print("⚠️ Сессия истекла, требуется переавторизация (auth page content)")
            return True
        
        if 'modules/crud.php?act=auth' in response.text and '<h4' in response.text and 'Авторизация' in response.text:
            print("⚠️ Сессия истекла, требуется переавторизация (auth form detected)")
            return True
        
        return False
    
    def _dump_html_to_log(self, plate_number, html_content):
        """Dump HTML response to log file when parsing fails"""
        from datetime import datetime
        import os
        
        log_file = "rbda_parse_errors.log"
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        separator = "=" * 80
        
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(f"\n{separator}\n")
                f.write(f"TIMESTAMP: {timestamp}\n")
                f.write(f"PLATE: {plate_number}\n")
                f.write(f"{separator}\n")
                f.write("HTML RESPONSE:\n")
                f.write(html_content)
                f.write(f"\n{separator}\n\n")
            
            logger.warning(f"HTML response for {plate_number} dumped to {log_file}")
        except Exception as e:
            logger.error(f"Failed to dump HTML to log: {e}")
    
    def _parse_vehicle_info(self, info_div):
        if not info_div:
            return {}
        
        vehicle_info = {}
        
        # Try to parse new HTML structure with <u> tags
        html_text = str(info_div)
        
        # Extract fields using regex patterns for the new structure
        patterns = {
            'plate': r'Номер автомобиля:\s*<u>([^<]+)</u>',
            'model': r'Модель автомобиля:\s*<u>([^<]+)</u>',
            'color': r'Цвет автомобиля:\s*<u>([^<]+)</u>',
            'fine_count': r'Кол-во штрафов:\s*<u>([^<]+)</u>',
            'total_amount': r'Общая сумма:\s*<u>([^<]+)</u>'
        }
        
        for key, pattern in patterns.items():
            match = re.search(pattern, html_text, re.IGNORECASE)
            if match:
                vehicle_info[key] = match.group(1).strip()
        
        # If new structure parsing failed, fall back to old method
        if not vehicle_info:
            text = info_div.get_text(separator='\n', strip=True)
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            for line in lines:
                if ':' in line:
                    key, value = line.split(':', 1)
                    key = key.strip()
                    value = value.strip()
                    
                    if 'Владелец' in key or 'Owner' in key:
                        vehicle_info['owner'] = value
                    elif 'Марка' in key or 'Brand' in key:
                        vehicle_info['brand'] = value
                    elif 'Модель' in key or 'Model' in key:
                        vehicle_info['model'] = value
                    elif 'Цвет' in key or 'Color' in key:
                        vehicle_info['color'] = value
                    elif 'Год' in key or 'Year' in key:
                        vehicle_info['year'] = value
                    elif 'VIN' in key.upper():
                        vehicle_info['vin'] = value
                    elif 'Номер' in key or 'Plate' in key:
                        vehicle_info['plate'] = value
        
        return vehicle_info

    def search_fines_by_plate(self, plate_number):
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=DeprecationWarning)
            
            if not self._login(): 
                return {"error": "Не удалось выполнить авторизацию."}
            
            search_payload = {'plate': plate_number.upper(),'srchfines': ''}
            print(f"🚀 Ищу штрафы для {plate_number}...")
            
            try:
                response = self.session.post(self.search_url, data=search_payload, timeout=self.timeout)
                response.raise_for_status()
                
                if self._check_session_expired(response):
                    print("🔄 Выполняю переавторизацию...")
                    if not self._login(force=True):
                        return {"error": "Сессия истекла, не удалось переавторизоваться."}
                    response = self.session.post(self.search_url, data=search_payload, timeout=self.timeout)
                    response.raise_for_status()
                    
                    if self._check_session_expired(response):
                        return {"error": "Сессия истекла после переавторизации."}
                
                soup = BeautifulSoup(response.text, 'html.parser')
                results = {"vehicle_info": {}, "fines": [], "pay_all_data": None}
                
                info_div = soup.find("div", class_="alert-primary")
                if info_div:
                    results["vehicle_info"] = self._parse_vehicle_info(info_div)
                
                # Parse "Pay All" form
                pay_all_form = soup.find('form', action='qrforpay.php')
                if pay_all_form:
                    summa_input = pay_all_form.find('input', {'name': 'summa'})
                    plate_input = pay_all_form.find('input', {'name': 'plate'})
                    if summa_input and plate_input:
                        results["pay_all_data"] = {
                            "summa": summa_input.get('value', ''),
                            "plate": plate_input.get('value', '')
                        }
                        print(f"✅ Найдена форма оплаты всех штрафов: {results['pay_all_data']}")

                fines_table = soup.find('table', class_='table-light')
                if fines_table and fines_table.find('tbody'):
                    for row in fines_table.find_all('tr'):
                        cells = row.find_all('td')
                        if len(cells) > 10:
                            fine_data = { 
                                "order": cells[1].text.strip(), 
                                "plate": cells[2].text.strip(), 
                                "date": cells[3].text.strip(), 
                                "violation": cells[4].text.strip(), 
                                "amount": cells[5].text.strip(), 
                                "media_links": {} 
                            }
                            media_names = ["Фото 1", "Фото 2", "Доп фото", "Видео"]
                            link_cells = cells[6:10]
                            for i, cell in enumerate(link_cells):
                                link_tag = cell.find('a')
                                if link_tag and link_tag.has_attr('href'):
                                    href = link_tag['href']
                                    if not href: 
                                        continue
                                    absolute_url = urllib.parse.urljoin(self.base_url, href)
                                    media_key = media_names[i].replace(' ', '_').lower()
                                    fine_data["media_links"][media_key] = absolute_url
                            results["fines"].append(fine_data)
                
                if not results.get("vehicle_info") and not results["fines"]:
                    # Log HTML response to file for debugging parsing issues
                    self._dump_html_to_log(plate_number, response.text)
                    return {"error": "Информация по данному номеру не найдена."}
                
                print("✅ Штрафы найдены и обработаны!")
                return results
                
            except requests.exceptions.Timeout:
                print("❌ Превышено время ожидания")
                return {"error": "Превышено время ожидания ответа от сервера."}
            except requests.exceptions.RequestException as e: 
                print(f"❌ Ошибка сети: {e}")
                return {"error": f"Ошибка сети при поиске штрафов: {e}"}

    async def get_direct_media_link_async(self, viewer_url: str) -> Optional[str]:
        """Async version of get_direct_media_link using aiohttp for speed"""
        # Rate limiting only for API calls, not media downloads
        await self._rate_limit_check()
        
        try:
            viewer_url = urllib.parse.urljoin(self.base_url, viewer_url)
            
            if 'video.mycar.tj/' in viewer_url:
                video_id = viewer_url.strip('/').split('/')[-1]
                direct_link = f"https://video.mycar.tj/video/download/video/{video_id}"
                print(f"✅ Преобразовал ссылку на видео: {direct_link}")
                return direct_link
            
            print(f"🔎 Ищу прямую ссылку на фото: {viewer_url}...")
            
            # Use aiohttp session
            session = await self._get_aiohttp_session()
            
            # Copy cookies from requests session to aiohttp
            cookies = {cookie.name: cookie.value for cookie in self.session.cookies}
            
            async with session.get(viewer_url, cookies=cookies, allow_redirects=True) as response:
                response.raise_for_status()
                
                # Check Content-Type to determine if it's an image or HTML
                content_type = response.headers.get('Content-Type', '').lower()
                
                # If response is already an image, return the URL directly
                if 'image/' in content_type or content_type.startswith('application/octet-stream'):
                    print(f"✅ URL уже является прямой ссылкой на изображение (Content-Type: {content_type})")
                    return str(response.url)
                
                # Otherwise, parse as HTML/text
                try:
                    html_text = await response.text()
                except UnicodeDecodeError:
                    # If we can't decode as text, it's likely binary data (image)
                    print(f"✅ Обнаружены бинарные данные, URL является прямой ссылкой")
                    return str(response.url)
                
                # Check for session expiration
                if 'login.php' in str(response.url) or 'Авторизация' in html_text:
                    print("🔄 Сессия истекла при доступе к медиа, переавторизация...")
                    # Re-authenticate using sync method
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, lambda: self._login(force=True))
                    
                    # Retry with new cookies
                    cookies = {cookie.name: cookie.value for cookie in self.session.cookies}
                    async with session.get(viewer_url, cookies=cookies, allow_redirects=True) as retry_response:
                        retry_response.raise_for_status()
                        
                        # Check Content-Type again for retry
                        retry_content_type = retry_response.headers.get('Content-Type', '').lower()
                        if 'image/' in retry_content_type or retry_content_type.startswith('application/octet-stream'):
                            print(f"✅ URL уже является прямой ссылкой на изображение (Content-Type: {retry_content_type})")
                            return str(retry_response.url)
                        
                        try:
                            html_text = await retry_response.text()
                        except UnicodeDecodeError:
                            print(f"✅ Обнаружены бинарные данные, URL является прямой ссылкой")
                            return str(retry_response.url)
                        
                        if 'login.php' in str(retry_response.url) or 'Авторизация' in html_text:
                            print("❌ Сессия истекла после переавторизации")
                            return None
                
                soup = BeautifulSoup(html_text, 'html.parser')
                img_tag = soup.select_one('body > img')
                if img_tag and img_tag.has_attr('src'):
                    absolute_src = urllib.parse.urljoin(self.base_url, img_tag['src'])
                    print(f"✅ Найдена прямая ссылка на фото: {absolute_src}")
                    return absolute_src
                return viewer_url
                
        except asyncio.TimeoutError:
            print(f"❌ Превышено время ожидания при поиске прямой ссылки")
            return None
        except aiohttp.ClientError as e:
            print(f"❌ Ошибка при поиске прямой ссылки: {e}")
            return None
        except Exception as e:
            print(f"❌ Неожиданная ошибка: {e}")
            return None

    def get_direct_media_link(self, viewer_url: str) -> Optional[str]:
        """Sync wrapper for backward compatibility"""
        try:
            viewer_url = urllib.parse.urljoin(self.base_url, viewer_url)
            
            if 'video.mycar.tj/' in viewer_url:
                video_id = viewer_url.strip('/').split('/')[-1]
                direct_link = f"https://video.mycar.tj/video/download/video/{video_id}"
                print(f"✅ Преобразовал ссылку на видео: {direct_link}")
                return direct_link
            
            print(f"🔎 Ищу прямую ссылку на фото: {viewer_url}...")
            response = self.session.get(viewer_url, timeout=self.timeout)
            response.raise_for_status()
            
            # Check Content-Type to determine if it's an image or HTML
            content_type = response.headers.get('Content-Type', '').lower()
            
            # If response is already an image, return the URL directly
            if 'image/' in content_type or content_type.startswith('application/octet-stream'):
                print(f"✅ URL уже является прямой ссылкой на изображение (Content-Type: {content_type})")
                return response.url
            
            if self._check_session_expired(response):
                print("🔄 Сессия истекла при доступе к медиа, переавторизация...")
                if self._login(force=True):
                    response = self.session.get(viewer_url, timeout=self.timeout)
                    response.raise_for_status()
                    
                    # Check Content-Type again for retry
                    retry_content_type = response.headers.get('Content-Type', '').lower()
                    if 'image/' in retry_content_type or retry_content_type.startswith('application/octet-stream'):
                        print(f"✅ URL уже является прямой ссылкой на изображение (Content-Type: {retry_content_type})")
                        return response.url
                    
                    if self._check_session_expired(response):
                        print("❌ Сессия истекла после переавторизации")
                        return None
                else:
                    return None
            
            # Try to parse as HTML/text
            try:
                soup = BeautifulSoup(response.text, 'html.parser')
                img_tag = soup.select_one('body > img')
                if img_tag and img_tag.has_attr('src'):
                    absolute_src = urllib.parse.urljoin(self.base_url, img_tag['src'])
                    print(f"✅ Найдена прямая ссылка на фото: {absolute_src}")
                    return absolute_src
                return viewer_url
            except UnicodeDecodeError:
                # If we can't decode as text, it's likely binary data (image)
                print(f"✅ Обнаружены бинарные данные, URL является прямой ссылкой")
                return response.url
        except requests.exceptions.Timeout:
            print(f"❌ Превышено время ожидания при поиске прямой ссылки")
            return None
        except requests.exceptions.RequestException as e:
            print(f"❌ Ошибка при поиске прямой ссылки: {e}")
            return None

    async def download_media_async(self, url: str) -> Optional[bytes]:
        """Async version of download_media with rate limiting and semaphore"""
        async with self.download_semaphore:  # Limit concurrent downloads
            await self._rate_limit_check()
            
            try:
                url = urllib.parse.urljoin(self.base_url, url)
                print(f"⬇️ Скачиваю {url}...")
                
                # Run blocking request in thread pool with streaming
                loop = asyncio.get_event_loop()
                
                def _download():
                    response = self.session.get(url, timeout=(5, 60), stream=True)
                    response.raise_for_status()
                    
                    if self._check_session_expired(response):
                        print("🔄 Сессия истекла при скачивании, переавторизация...")
                        if self._login(force=True):
                            response = self.session.get(url, timeout=(5, 60), stream=True)
                            response.raise_for_status()
                            
                            if self._check_session_expired(response):
                                print("❌ Сессия истекла после переавторизации")
                                return None
                        else:
                            return None
                    
                    # Read in 64KB chunks for better performance
                    chunks = []
                    for chunk in response.iter_content(chunk_size=65536):  # 64KB chunks
                        if chunk:
                            chunks.append(chunk)
                    
                    return b''.join(chunks)
                
                content = await loop.run_in_executor(None, _download)
                
                if content:
                    print("✅ Скачивание завершено.")
                    return content
                return None
                
            except requests.exceptions.Timeout:
                print(f"❌ Превышено время ожидания при скачивании")
                return None
            except requests.exceptions.RequestException as e:
                print(f"❌ Ошибка при скачивании {url}: {e}")
                return None
            except Exception as e:
                print(f"❌ Неожиданная ошибка при скачивании: {e}")
                return None

    def download_media(self, url: str) -> Optional[bytes]:
        """Sync wrapper for backward compatibility"""
        try:
            url = urllib.parse.urljoin(self.base_url, url)
            print(f"⬇️ Скачиваю {url}...")
            
            # Use streaming and larger chunks
            response = self.session.get(url, timeout=(5, 60), stream=True)
            response.raise_for_status()
            
            if self._check_session_expired(response):
                print("🔄 Сессия истекла при скачивании, переавторизация...")
                if self._login(force=True):
                    response = self.session.get(url, timeout=(5, 60), stream=True)
                    response.raise_for_status()
                    
                    if self._check_session_expired(response):
                        print("❌ Сессия истекла после переавторизации")
                        return None
                else:
                    return None
            
            # Read in 64KB chunks for better performance
            chunks = []
            for chunk in response.iter_content(chunk_size=65536):  # 64KB chunks
                if chunk:
                    chunks.append(chunk)
            
            content = b''.join(chunks)
            print("✅ Скачивание завершено.")
            return content
            
        except requests.exceptions.Timeout:
            print(f"❌ Превышено время ожидания при скачивании")
            return None
        except requests.exceptions.RequestException as e:
            print(f"❌ Ошибка при скачивании {url}: {e}")
            return None

    async def download_multiple_media_async(self, urls: list[str]) -> list[Optional[bytes]]:
        """Download multiple media files in parallel with rate limiting"""
        if not urls:
            return []
        
        print(f"📥 Начинаю параллельную загрузку {len(urls)} файлов...")
        tasks = [self.download_media_async(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Convert exceptions to None
        results = [r if not isinstance(r, Exception) else None for r in results]
        
        successful = sum(1 for r in results if r is not None)
        print(f"✅ Загружено {successful} из {len(urls)} файлов")
        
        return results

    async def download_media_optimized(self, media_urls: list[str], optimization_enabled: bool = True) -> list[Optional[bytes]]:
        """
        Download media files with optimization control.
        
        Args:
            media_urls: List of media URLs to download
            optimization_enabled: If True, use parallel downloads with larger buffers.
                                 If False, use sequential downloads with smaller buffers.
        
        Returns:
            List of downloaded file data (bytes) or None for failed downloads
        """
        if not media_urls:
            return []
        
        import logging
        logger = logging.getLogger(__name__)
        
        start_time = time.time()
        
        if optimization_enabled:
            # ULTRA TURBO MODE: Unlimited parallel downloads for maximum speed
            logger.info(f"⚡ ULTRA TURBO MODE: Downloading {len(media_urls)} files in parallel (NO LIMIT)")
            print(f"⚡ ULTRA TURBO MODE: Downloading {len(media_urls)} files in parallel")
            
            # NO SEMAPHORE - download ALL files at once for maximum throughput!
            # aiohttp handles connection pooling internally
            tasks = [self._download_single_file_optimized(url, chunk_size=262144, max_retries=3) for url in media_urls]
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Filter out exceptions
            valid_results = []
            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    logger.error(f"❌ Failed to download {media_urls[i]}: {result}")
                    valid_results.append(None)
                else:
                    valid_results.append(result)
            
            elapsed = time.time() - start_time
            successful = sum(1 for r in valid_results if r is not None)
            logger.info(
                f"📊 ULTRA TURBO: Downloaded {successful}/{len(media_urls)} files in {elapsed:.2f}s "
                f"(avg: {elapsed/max(successful, 1):.2f}s per file)"
            )
            print(f"✅ ULTRA TURBO: Downloaded {successful}/{len(media_urls)} files in {elapsed:.2f}s")
            
            return valid_results
        else:
            # NORMAL MODE: Sequential downloads with smaller buffers
            logger.info(f"🐌 NORMAL MODE: Downloading {len(media_urls)} files sequentially")
            print(f"🐌 NORMAL MODE: Downloading {len(media_urls)} files sequentially")
            
            results = []
            for i, url in enumerate(media_urls, 1):
                logger.info(f"⬇️ Downloading file {i}/{len(media_urls)}: {url}")
                try:
                    result = await self._download_single_file_optimized(url, chunk_size=65536, max_retries=3)
                    results.append(result)
                except Exception as e:
                    logger.error(f"❌ Failed to download {url}: {e}")
                    results.append(None)
            
            elapsed = time.time() - start_time
            successful = sum(1 for r in results if r is not None)
            logger.info(
                f"📊 NORMAL: Downloaded {successful}/{len(media_urls)} files in {elapsed:.2f}s "
                f"(avg: {elapsed/max(successful, 1):.2f}s per file)"
            )
            print(f"✅ NORMAL: Downloaded {successful}/{len(media_urls)} files in {elapsed:.2f}s")
            
            return results

    async def _download_single_file_optimized(
        self,
        url: str,
        chunk_size: int = 262144,  # 256KB chunks for maximum speed
        max_retries: int = 3
    ) -> Optional[bytes]:
        """
        Download a single file with retry logic and configurable chunk size using aiohttp.
        
        Args:
            url: URL to download
            chunk_size: Size of chunks to read (256KB for turbo, 64KB for normal)
            max_retries: Maximum number of retry attempts
        
        Returns:
            Downloaded file data or None on failure
        """
        import logging
        logger = logging.getLogger(__name__)
        
        # Make URL absolute
        url = urllib.parse.urljoin(self.base_url, url)
        
        # Get aiohttp session
        session = await self._get_aiohttp_session()
        
        for attempt in range(max_retries):
            try:
                # Use aiohttp for true async downloads - NO rate limiting for downloads
                async with session.get(url, allow_redirects=True) as response:
                    response.raise_for_status()
                    
                    # Read entire content at once for maximum speed
                    content = await response.read()
                    
                    if content:
                        logger.debug(f"✅ Downloaded {url} (size: {len(content)} bytes, attempt: {attempt + 1})")
                        return content
                
                return None
                
            except asyncio.TimeoutError:
                if attempt < max_retries - 1:
                    # Faster backoff: 0.3, 0.6, 1.2 seconds for quicker retries
                    wait_time = 0.3 * (2 ** attempt)
                    logger.warning(f"⚠️ Timeout downloading {url} (attempt {attempt + 1}), retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"❌ Timeout downloading {url} after {max_retries} attempts")
                    return None
            
            except aiohttp.ClientError as e:
                if attempt < max_retries - 1:
                    # Faster backoff: 0.3, 0.6, 1.2 seconds for quicker retries
                    wait_time = 0.3 * (2 ** attempt)
                    logger.warning(f"⚠️ Error downloading {url} (attempt {attempt + 1}): {e}, retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(f"❌ Failed to download {url} after {max_retries} attempts: {e}")
                    return None
            
            except Exception as e:
                logger.error(f"❌ Unexpected error downloading {url}: {e}")
                return None
        
        return None