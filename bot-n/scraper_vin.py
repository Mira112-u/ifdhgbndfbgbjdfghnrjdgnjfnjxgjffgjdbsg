# scraper_vin.py
import requests
from bs4 import BeautifulSoup

class ClientCardScraper:
    def __init__(self, login, password):
        self.auth_url = "https://rbda.dc.tj/modules/crud.php?act=auth"
        self.search_url = "https://rbda.dc.tj/pages/clientcard.php"
        self.login = login
        self.password = password
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
            "Origin": "https://rbda.dc.tj",
        })

    def _login(self) -> bool:
        """✅ ИСПРАВЛЕННАЯ, НАДЕЖНАЯ АВТОРИЗАЦИЯ."""
        self.session.cookies.clear()
        # Сначала делаем GET-запрос на главную, чтобы получить первичные куки
        try:
            self.session.get("https://rbda.dc.tj/index.php", timeout=10)
        except requests.exceptions.RequestException:
            pass # Не страшно, если не получится

        payload = {'login': self.login, 'password': self.password}
        print(f"🚀 Выполняю авторизацию...")
        try:
            # Отправляем POST-запрос на URL авторизации
            response = self.session.post(self.auth_url, data=payload, allow_redirects=False, timeout=15) # allow_redirects=False
            response.raise_for_status()
            
            # Успешный логин должен вернуть редирект (статус 302) на dashboard.php
            if response.status_code == 302 and 'dashboard.php' in response.headers.get('Location', ''):
                 print("✅ Авторизация прошла успешно! Получен редирект.")
                 return True
            
            print(f"❌ Ошибка авторизации: получен статус {response.status_code}, ожидался 302.")
            return False
        except requests.exceptions.RequestException as e:
            print(f"❌ Ошибка сети при авторизации: {e}")
            return False
            
    def get_client_card_info(self, vin_or_plate: str):
        # Сначала пытаемся сделать запрос. Если сессия "живая", он пройдет.
        print(f"🚀 Ищу карту клиента для {vin_or_plate}...")
        try:
            payload = {'plate': vin_or_plate.upper(), 'srchfines': ''}
            response = self.session.post(self.search_url, data=payload, timeout=15)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            
            # Если мы на странице входа - логинимся
            if "Авторизация" in soup.title.string:
                print("⚠️ Сессия недействительна или истекла. Выполняю вход...")
                if not self._login():
                    return {"error": "Не удалось выполнить авторизацию. Проверьте учетные данные."}
                
                # Повторяем запрос после успешного входа
                print(f"🚀 Повторный запрос карты клиента для {vin_or_plate}...")
                response = self.session.post(self.search_url, data=payload, timeout=15)
                response.raise_for_status()
                soup = BeautifulSoup(response.text, 'html.parser')

            print("✅ Страница с картой клиента получена! Начинаю парсинг...")
            
            results = {}
            data_map = {'Автомобиль': 'car', 'Водитель': 'driver', 'Документы': 'docs'}
            
            all_headers = soup.find_all("h5", class_="card-title")
            
            for header in all_headers:
                header_text = header.text.strip()
                result_key = data_map.get(header_text)
                if result_key:
                    results[result_key] = {}
                    table = header.find_next("table", class_="table")
                    if not table: continue
                    for row in table.find("tbody").find_all("tr"):
                        cells = row.find_all("td")
                        if len(cells) >= 2:
                            label = cells[0].text.strip()
                            value = " ".join(c.text.strip() for c in cells[1:])
                            if label:
                                results[result_key][label] = value
            
            photos_header = soup.find("h5", class_="card-title", text=lambda t: t and "Фото" in t)
            if photos_header:
                photo_links = []
                photo_container = photos_header.find_next("p")
                if photo_container:
                    for img_tag in photo_container.find_all("img"):
                        if img_tag.has_attr('src') and img_tag['src']:
                            photo_links.append(img_tag['src'])
                if photo_links:
                    results['photos'] = photo_links

            if not results:
                return {"error": "Информация по данному номеру/VIN не найдена на странице."}

            return results
        except requests.exceptions.RequestException as e:
            return {"error": f"Ошибка сети: {e}"}
