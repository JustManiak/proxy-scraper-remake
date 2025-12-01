import requests
import re
import threading
from threading import Lock, BoundedSemaphore
import time
import argparse
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import json
import sys
import urllib3
import os
from datetime import datetime

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

class ProxyChecker:
    def __init__(self, output_file="proxies.json", max_threads=500):
        self.lock = Lock()
        self.file_lock = Lock()
        self.checked_count = 0
        self.valid_count = 0
        self.scraped_count = 0
        self.output_file = output_file
        self.max_threads = max_threads
        self.last_run = None
        self._initialize_output_file()

    def _initialize_output_file(self):
        with self.lock:
            with open(self.output_file, 'w') as f:
                json.dump({
                    "proxies": [], 
                    "count": 0, 
                    "last_updated": time.time(),
                    "last_check": None
                }, f)

    def _add_proxy_to_file(self, proxy_string):
        with self.file_lock:
            try:
                with open(self.output_file, 'r') as f:
                    data = json.load(f)

                data["proxies"].append(proxy_string)
                data["count"] = len(data["proxies"])
                data["last_updated"] = time.time()

                temp_file = f"{self.output_file}.tmp"
                with open(temp_file, 'w') as f:
                    json.dump(data, f, separators=(',', ':'))

                os.replace(temp_file, self.output_file)

            except Exception as e:
                print(f"\n[Error] Failed to write proxy: {e}")

    def _read_proxies_from_file(self):
        try:
            with open(self.output_file, 'r') as f:
                data = json.load(f)
                return data.get("proxies", []), data.get("count", 0)
        except:
            return [], 0

    def reset_counters(self):
        with self.lock:
            self.checked_count = 0
            self.valid_count = 0
            self.scraped_count = 0
            self.last_run = datetime.now()

    def check_single_proxy(self, proxy, proxy_types, retries=2, timeout=2):
        is_valid = False
        valid_type = None

        for proxy_type in proxy_types:
            try:
                for attempt in range(retries):
                    try:
                        proxies = {
                            "http": f"{proxy_type}://{proxy}",
                            "https": f"{proxy_type}://{proxy}"
                        }
                        with requests.Session() as session:
                            session.trust_env = False
                            response = session.get("http://httpbin.org/ip", 
                                                  proxies=proxies, 
                                                  timeout=timeout, 
                                                  verify=False)
                            if response.status_code == 200:
                                is_valid = True
                                valid_type = proxy_type
                                break
                    except (requests.exceptions.ConnectionError, 
                           requests.exceptions.Timeout, 
                           ConnectionResetError,
                           requests.exceptions.ProxyError):
                        if attempt < retries - 1:
                            time.sleep(0.05)
                        continue
                    except Exception:
                        break
                if is_valid:
                    break
            except Exception:
                continue

        if is_valid:
            proxy_entry = f"{valid_type}://{proxy}"
            self._add_proxy_to_file(proxy_entry)

            with self.file_lock:
                with open("Valid_Proxies.txt", "a", buffering=1) as f:
                    f.write(f"{proxy_entry}\n")

            with self.lock:
                self.valid_count += 1

        with self.lock:
            self.checked_count += 1
            if self.checked_count % 10 == 0:
                print(f'\rChecked: {self.checked_count}/{self.scraped_count} | Valid: {self.valid_count}', 
                      end='', flush=True)

        return is_valid, valid_type if is_valid else None

    def check_proxy_batch(self, proxy, proxy_types, save_invalid, semaphore):
        is_valid = False
        with semaphore:
            for proxy_type in proxy_types:
                try:
                    for attempt in range(2):
                        try:
                            proxies = {
                                "http": f"{proxy_type}://{proxy}",
                                "https": f"{proxy_type}://{proxy}"
                            }
                            with requests.Session() as session:
                                session.trust_env = False
                                response = session.get("http://httpbin.org/ip", 
                                                     proxies=proxies, 
                                                     timeout=2, 
                                                     verify=False)
                                if response.status_code == 200:
                                    is_valid = True

                                    proxy_entry = f"{proxy_type}://{proxy}"
                                    self._add_proxy_to_file(proxy_entry)

                                    with self.file_lock:
                                        with open("Valid_Proxies.txt", "a", buffering=1) as f:
                                            f.write(f"{proxy_entry}\n")

                                    with self.lock:
                                        self.valid_count += 1
                                    break
                        except (requests.exceptions.ConnectionError, 
                               requests.exceptions.Timeout, 
                               ConnectionResetError,
                               requests.exceptions.ProxyError):
                            if attempt < 1:
                                time.sleep(0.05)
                            continue
                        except Exception:
                            break
                    if is_valid:
                        break
                except Exception:
                    continue

            if not is_valid and save_invalid:
                with self.file_lock:
                    with open("Invalid_Proxies.txt", "a", buffering=1) as f:
                        f.write(f"{proxy}\n")

            with self.lock:
                self.checked_count += 1
                if self.checked_count % 10 == 0:
                    print(f'\rChecked: {self.checked_count}/{self.scraped_count} | Valid: {self.valid_count}', 
                          end='', flush=True)

    def scrape_proxies(self, urls):
        print("\n[Status] Scraping Proxies...")
        proxies = set()
        ip_port_pattern = re.compile(r'^\d{1,3}(?:\.\d{1,3}){3}:\d{2,5}$')

        for url in urls:
            for attempt in range(2):
                try:
                    response = requests.get(url, timeout=10, verify=False)
                    if response.status_code == 200:
                        for line in response.text.splitlines():
                            line = line.strip()
                            if ip_port_pattern.match(line) and not line.startswith("127"):
                                proxies.add(line)
                        break
                except Exception:
                    if attempt < 1:
                        time.sleep(0.2)
                    continue

        return proxies

    def check_from_file(self, filepath, proxy_types, save_invalid):
        print(f"\n[Status] Checking proxies from {filepath}...")

        with open(filepath, "r") as file:
            proxies = [line.strip() for line in file if line.strip()]

        self.scraped_count = len(proxies)
        self._process_proxy_batch(proxies, proxy_types, save_invalid)

        print(f'\n\nChecking complete! Total: {self.checked_count}, Valid: {self.valid_count}')

    def scrape_and_check(self, urls, proxy_types, save_invalid):
        proxies = self.scrape_proxies(urls)
        self.scraped_count = len(proxies)
        print(f"\n[Status] Found {self.scraped_count} proxies. Checking...")

        proxy_list = list(proxies)
        self._process_proxy_batch(proxy_list, proxy_types, save_invalid)

        print(f'\n\nScraping and checking complete! Total: {self.checked_count}, Valid: {self.valid_count}')

    def _process_proxy_batch(self, proxies, proxy_types, save_invalid):
        semaphore = BoundedSemaphore(self.max_threads)
        threads = []

        batch_size = 500
        for i in range(0, len(proxies), batch_size):
            batch = proxies[i:i+batch_size]

            for proxy in batch:
                thread = threading.Thread(target=self.check_proxy_batch, 
                                        args=(proxy, proxy_types, save_invalid, semaphore),
                                        daemon=True)
                threads.append(thread)
                thread.start()

            if i + batch_size < len(proxies):
                for thread in threads[-len(batch):]:
                    thread.join()

        for thread in threads:
            thread.join()


class ProxyHTTPHandler(BaseHTTPRequestHandler):
    checker = None

    def log_message(self, format, *args):
        pass

    def do_GET(self):
        try:
            parsed_path = urlparse(self.path)

            if parsed_path.path == '/get_proxies':
                try:
                    with open(self.checker.output_file, 'r') as f:
                        data = json.load(f)

                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Connection', 'close')
                    self.end_headers()

                    self.wfile.write(json.dumps(data, separators=(',', ':')).encode())
                except Exception as e:
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': str(e)}).encode())

            elif parsed_path.path == '/check_proxy':
                query_params = parse_qs(parsed_path.query)
                proxy_param = query_params.get('check_proxy', [None])[0]

                if not proxy_param:
                    self.send_response(400)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Missing check_proxy parameter'}).encode())
                    return

                print(f"\n[API] Checking proxy: {proxy_param}")
                proxy_types = ["http", "https", "socks4", "socks5"]

                if self.checker:
                    is_valid, valid_type = self.checker.check_single_proxy(proxy_param, proxy_types)

                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Connection', 'close')
                    self.end_headers()

                    response = {
                        'proxy': proxy_param,
                        'valid': is_valid,
                        'type': valid_type
                    }
                    self.wfile.write(json.dumps(response, separators=(',', ':')).encode())
                else:
                    self.send_response(500)
                    self.send_header('Content-type', 'application/json')
                    self.send_header('Connection', 'close')
                    self.end_headers()
                    self.wfile.write(json.dumps({'error': 'Checker not initialized'}).encode())

            elif parsed_path.path == '/stats':
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Connection', 'close')
                self.end_headers()

                stats = {
                    'checked': self.checker.checked_count if self.checker else 0,
                    'valid': self.checker.valid_count if self.checker else 0,
                    'total': self.checker.scraped_count if self.checker else 0,
                    'output_file': self.checker.output_file if self.checker else None,
                    'last_run': self.checker.last_run.isoformat() if self.checker and self.checker.last_run else None
                }
                self.wfile.write(json.dumps(stats, separators=(',', ':')).encode())

            else:
                self.send_response(404)
                self.send_header('Content-type', 'application/json')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Not found'}).encode())

        except (ConnectionAbortedError, BrokenPipeError, ConnectionResetError):
            pass
        except Exception:
            try:
                self.send_response(500)
                self.send_header('Content-type', 'application/json')
                self.send_header('Connection', 'close')
                self.end_headers()
                self.wfile.write(json.dumps({'error': 'Internal error'}).encode())
            except:
                pass


def start_web_server(port, checker):
    ProxyHTTPHandler.checker = checker
    server = ThreadingHTTPServer(('0.0.0.0', port), ProxyHTTPHandler)
    server.daemon_threads = True
    print(f"\n[Web Server] Started on http://localhost:{port}")
    print(f"[Web Server] Endpoints:")
    print(f"  - GET /get_proxies - Get all valid proxies from {checker.output_file}")
    print(f"  - GET /check_proxy?check_proxy=IP:PORT - Check a specific proxy")
    print(f"  - GET /stats - Get current statistics")
    server.serve_forever()


def get_default_urls(proxy_type):
    urls = [
                "https://openproxy.space/list/http",
                "https://github.com/zloi-user/hideip.me/blob/main/socks5.txt",
                "https://github.com/zloi-user/hideip.me/blob/main/socks4.txt",
                "https://github.com/zloi-user/hideip.me/blob/main/http.txt",
                "https://github.com/zloi-user/hideip.me/blob/main/https.txt",
                "https://github.com/zloi-user/hideip.me/blob/main/connect.txt",
                "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt",
                "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt",
                "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
                "https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
                "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt",
                "https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt",
                "https://raw.githubusercontent.com/proxy4parsing/proxy-list/main/http.txt",
                "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt",
                "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_anonymous/http.txt",
                "https://www.proxy-list.download/api/v1/get?type=https",
                "https://www.proxy-list.download/api/v1/get?type=http",
                "https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt",
                "https://raw.githubusercontent.com/mmpx12/proxy-list/master/https.txt",
                "https://api.openproxylist.xyz/http.txt",
                "http://pubproxy.com/api/proxy",
                "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-https.txt",
                "https://api.openproxylist.xyz/socks4.txt",
                "https://www.proxy-list.download/api/v1/get?type=socks4",
                "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks4.txt",
                "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks4.txt",
                "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt",
                "https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks4.txt",
                "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt",
                "https://openproxy.space/list/socks4",
                "https://github.com/zloi-user/hideip.me/blob/main/http.txt",
                "https://github.com/zloi-user/hideip.me/blob/main/https.txt",
                "https://github.com/zloi-user/hideip.me/blob/main/socks4.txt",
                "https://github.com/zloi-user/hideip.me/blob/main/socks5.txt",
                "https://github.com/zloi-user/hideip.me/blob/main/connect.txt",
                "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_anonymous/socks4.txt",
                "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS4_RAW.txt",
                "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks4&timeout=10000&country=all",
                "https://raw.githubusercontent.com/thespeedx/proxy-list/master/socks5.txt",
                "https://api.openproxylist.xyz/socks5.txt",
                "https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt",
                "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt",
                "https://www.proxy-list.download/api/v1/get?type=socks5",
                "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt",
                "https://openproxy.space/list/socks5",
                "https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks5.txt",
                "https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all",
                "https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks5.txt",
                "https://raw.githubusercontent.com/mmpx12/proxy-list/master/proxies.txt",
                "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_anonymous/socks5.txt",
                "https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks5.txt",
                "https://raw.githubusercontent.com/shiftytr/proxy-list/master/socks5.txt",
                "https://raw.githubusercontent.com/IshanSingla/proxy-list/main/proxys/http.txt",
                "https://raw.githubusercontent.com/IshanSingla/proxy-list/main/proxys/https.txt",
                "https://raw.githubusercontent.com/IshanSingla/proxy-list/main/proxys/socks4.txt",
                "https://raw.githubusercontent.com/IshanSingla/proxy-list/main/proxys/proxys.txt",
                "https://raw.githubusercontent.com/almroot/proxylist/master/list.txt",
                "https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/proxies.txt",
                "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt",
                "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks4.txt",
                "https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt",
                "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt",
                "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/socks4.txt",
                "https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/socks5.txt",
                "https://spys.one/free-proxy-list/",
                "https://proxies.gg/proxy-list",
                "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=ipport&format=text&anonymity=Anonymous,Elite&timeout=619",
                "https://proxydb.net/?protocol=http&protocol=https&protocol=socks4&protocol=socks5&anonlvl=4",
                "https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&anonymity=Elite,Anonymous&timeout=420",
                "https://raw.githubusercontent.com/monosans/proxy-list/refs/heads/main/proxies/socks5.txt",
                "https://raw.githubusercontent.com/monosans/proxy-list/refs/heads/main/proxies/socks4.txt",
                "https://raw.githubusercontent.com/monosans/proxy-list/refs/heads/main/proxies/http.txt",
                "https://raw.githubusercontent.com/monosans/proxy-list/refs/heads/main/proxies_anonymous/http.txt",
                'https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/all/data.txt',
                'https://sunny9577.github.io/proxy-scraper/generated/socks5_proxies.txt',
                'http://raw.githubusercontent.com/shiftytr/proxy-list/master/socks5.txt',
                'https://spys.me/socks.txt',
                'https://proxyspace.pro/socks5.txt',
                'https://api.openproxylist.xyz/socks5.txt',
                'https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/socks5.txt',
                'https://raw.githubusercontent.com/tuanminpay/live-proxy/master/socks5.txt',
                'https://raw.githubusercontent.com/Anonym0usWork1221/Free-Proxies/main/proxy_files/socks5_proxies.txt',
                'https://raw.githubusercontent.com/BreakingTechFr/Proxy_Free/main/proxies/socks5.txt',
                'https://raw.githubusercontent.com/yemixzy/proxy-list/main/proxies/socks5.txt',
                'https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt',
                'https://raw.githubusercontent.com/Noctiro/getproxy/master/file/socks5.txt',
                'https://raw.githubusercontent.com/zevtyardt/proxy-list/main/socks5.txt',
                'https://raw.githubusercontent.com/zenjahid/FreeProxy4u/master/socks5.txt',
            ]

    socks5_list = [
                'https://github.com/zloi-user/hideip.me/blob/main/socks5.txt',
                'https://raw.githubusercontent.com/thespeedx/proxy-list/master/socks5.txt',
                'https://api.openproxylist.xyz/socks5.txt',
                'https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt',
                'https://www.proxy-list.download/api/v1/get?type=socks5',
                'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks5.txt',
                'https://openproxy.space/list/socks5',
                'https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks5.txt',
                'https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks5&timeout=10000&country=all',
                'https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks5.txt',
                'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_anonymous/socks5.txt',
                'https://raw.githubusercontent.com/shiftytr/proxy-list/master/socks5.txt',
                'https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks5.txt',
                'https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/socks5.txt',
                'https://proxydb.net/?protocol=http&protocol=https&protocol=socks4&protocol=socks5&anonlvl=4',
                'https://raw.githubusercontent.com/monosans/proxy-list/refs/heads/main/proxies/socks5.txt',
                'https://raw.githubusercontent.com/monosans/proxy-list/refs/heads/main/proxies_anonymous/socks5.txt',
                'https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/protocols/socks5/data.txt',
                'https://sunny9577.github.io/proxy-scraper/generated/socks5_proxies.txt',
                'http://raw.githubusercontent.com/shiftytr/proxy-list/master/socks5.txt',
                'https://spys.me/socks.txt',
                'https://proxyspace.pro/socks5.txt',
                'https://raw.githubusercontent.com/ErcinDedeoglu/proxies/main/proxies/socks5.txt',
                'https://raw.githubusercontent.com/tuanminpay/live-proxy/master/socks5.txt',
                'https://raw.githubusercontent.com/Anonym0usWork1221/Free-Proxies/main/proxy_files/socks5_proxies.txt',
                'https://raw.githubusercontent.com/BreakingTechFr/Proxy_Free/main/proxies/socks5.txt',
                'https://raw.githubusercontent.com/yemixzy/proxy-list/main/proxies/socks5.txt',
                'https://raw.githubusercontent.com/proxifly/free-proxy-list/main/proxies/protocols/socks5/data.txt',
                'https://raw.githubusercontent.com/Noctiro/getproxy/master/file/socks5.txt',
                'https://raw.githubusercontent.com/zevtyardt/proxy-list/main/socks5.txt',
                'https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5_RAW.txt',
                'https://raw.githubusercontent.com/mmpx12/proxy-list/master/proxies.txt',
                'https://raw.githubusercontent.com/zenjahid/FreeProxy4u/master/socks5.txt',
                'https://raw.githubusercontent.com/vakhov/fresh-proxy-list/master/socks5.txt'
            ]

    socks4_list = [
                'https://github.com/zloi-user/hideip.me/blob/main/socks4.txt',
                'https://api.openproxylist.xyz/socks4.txt',
                'https://www.proxy-list.download/api/v1/get?type=socks4',
                'https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-socks4.txt',
                'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/socks4.txt',
                'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/socks4.txt',
                'https://raw.githubusercontent.com/mmpx12/proxy-list/master/socks4.txt',
                'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/socks4.txt',
                'https://openproxy.space/list/socks4',
                'https://github.com/zloi-user/hideip.me/blob/main/socks4.txt',
                'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_anonymous/socks4.txt',
                'https://api.proxyscrape.com/v2/?request=getproxies&protocol=socks4&timeout=10000&country=all',
                'https://raw.githubusercontent.com/IshanSingla/proxy-list/main/proxys/socks4.txt',
                'https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/socks4.txt',
                'https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/socks4.txt',
                'https://proxydb.net/?protocol=http&protocol=https&protocol=socks4&protocol=socks5&anonlvl=4',
                'https://raw.githubusercontent.com/monosans/proxy-list/refs/heads/main/proxies/socks4.txt',
                'https://raw.githubusercontent.com/monosans/proxy-list/refs/heads/main/proxies_anonymous/socks4.txt',
                'https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/protocols/socks4/data.txt',
                'https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS4_RAW.txt',
                'https://raw.githubusercontent.com/mmpx12/proxy-list/master/proxies.txt',
            ]

    http_list = [
                'https://openproxy.space/list/http',
                'https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-http.txt',
                'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt',
                'https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt',
                'https://api.proxyscrape.com/v2/?request=getproxies&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all',
                'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/https.txt',
                'https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS_RAW.txt',
                'https://raw.githubusercontent.com/proxy4parsing/proxy-list/main/http.txt',
                'https://raw.githubusercontent.com/ShiftyTR/Proxy-List/master/http.txt',
                'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies_anonymous/http.txt',
                'https://www.proxy-list.download/api/v1/get?type=https',
                'https://www.proxy-list.download/api/v1/get?type=http',
                'https://raw.githubusercontent.com/mmpx12/proxy-list/master/http.txt',
                'https://raw.githubusercontent.com/mmpx12/proxy-list/master/https.txt',
                'https://api.openproxylist.xyz/http.txt',
                'http://pubproxy.com/api/proxy',
                'https://raw.githubusercontent.com/jetkai/proxy-list/main/online-proxies/txt/proxies-https.txt',
                'https://raw.githubusercontent.com/mmpx12/proxy-list/master/proxies.txt',
                'https://raw.githubusercontent.com/IshanSingla/proxy-list/main/proxys/http.txt',
                'https://raw.githubusercontent.com/IshanSingla/proxy-list/main/proxys/https.txt',
                'https://raw.githubusercontent.com/IshanSingla/proxy-list/main/proxys/proxys.txt',
                'https://raw.githubusercontent.com/almroot/proxylist/master/list.txt',
                'https://raw.githubusercontent.com/sunny9577/proxy-scraper/master/proxies.txt',
                'https://raw.githubusercontent.com/TheSpeedX/SOCKS-List/master/http.txt',
                'https://raw.githubusercontent.com/MuRongPIG/Proxy-Master/main/http.txt',
                'https://spys.one/free-proxy-list/',
                'https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=ipport&format=text&anonymity=Anonymous,Elite&timeout=619',
                'https://api.proxyscrape.com/v4/free-proxy-list/get?request=display_proxies&proxy_format=protocolipport&format=text&anonymity=Elite,Anonymous&timeout=420',
                'https://raw.githubusercontent.com/monosans/proxy-list/refs/heads/main/proxies/http.txt',
                'https://raw.githubusercontent.com/monosans/proxy-list/refs/heads/main/proxies_anonymous/http.txt',
                'https://raw.githubusercontent.com/proxifly/free-proxy-list/refs/heads/main/proxies/all/data.txt',
                'https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt'
            ]

    if proxy_type.lower() in ['http', 'https']:
        return http_list
    elif proxy_type.lower() == 'socks4':
        return socks4_list
    elif proxy_type.lower() == 'socks5':
        return socks5_list
    else:
        return urls



def run_check_cycle(checker, args, proxy_types):
    checker._initialize_output_file()

    with open("Valid_Proxies.txt", "w") as f:
        f.write("")
    if args.save_invalid:
        with open("Invalid_Proxies.txt", "w") as f:
            f.write("")

    checker.reset_counters()

    with checker.file_lock:
        try:
            with open(checker.output_file, 'r') as f:
                data = json.load(f)
            data['last_check'] = datetime.now().isoformat()
            with open(checker.output_file, 'w') as f:
                json.dump(data, f, separators=(',', ':'))
        except:
            pass

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n{'='*60}")
    print(f"[{timestamp}] Starting proxy check cycle...")
    print(f"{'='*60}")

    if args.mode == 'file':
        checker.check_from_file(args.file, proxy_types, args.save_invalid)
    elif args.mode == 'scrape':
        urls = get_default_urls(args.proxy_type)
        checker.scrape_and_check(urls, proxy_types, args.save_invalid)

    print(f"\n[Success] Valid proxies saved to: {args.output}")
    print(f"[Success] Backup text file: Valid_Proxies.txt")
    if args.save_invalid:
        print(f"[Success] Invalid proxies: Invalid_Proxies.txt")


def main():
    parser = argparse.ArgumentParser(description='High-Performance Proxy Checker with JSON Output and Scheduling')
    parser.add_argument('-m','--mode', choices=['file', 'scrape'], required=True,
                       help='Mode: check from file or scrape and check')
    parser.add_argument('-f','--file', help='Path to proxy file (required for file mode)')
    parser.add_argument('-o','--output', default='proxies.json',
                       help='Output JSON file (default: proxies.json)')
    parser.add_argument('-pt','--proxy-type', choices=['http', 'https', 'socks4', 'socks5', 'all'],
                       default='all', help='Proxy type to check')
    parser.add_argument('-si','--save-invalid', action='store_true',
                       help='Save invalid proxies to file')
    parser.add_argument('-p','--port', type=int, default=8080,
                       help='Web server port (default: 8080)')
    parser.add_argument('-t','--threads', type=int, default=500,
                       help='Maximum concurrent threads (default: 500)')
    parser.add_argument('-r','--repeat', type=int, default=0,
                       help='Repeat check every X hours (0=run once, default: 0)')

    args = parser.parse_args()

    if args.mode == 'file' and not args.file:
        print("Error: --file is required for file mode")
        sys.exit(1)

    if args.proxy_type in ['http', 'https']:
        proxy_types = ['http', 'https']
    elif args.proxy_type == 'socks4':
        proxy_types = ['socks4']
    elif args.proxy_type == 'socks5':
        proxy_types = ['socks5']
    else:
        proxy_types = ['http', 'https', 'socks4', 'socks5']

    checker = ProxyChecker(output_file=args.output, max_threads=args.threads)

    server_thread = threading.Thread(target=start_web_server, args=(args.port, checker), daemon=True)
    server_thread.start()

    print(f"[Config] Output file: {args.output}")
    print(f"[Config] Proxy type: {args.proxy_type}")
    print(f"[Config] Max threads: {args.threads}")
    if args.repeat > 0:
        print(f"[Config] Repeat every: {args.repeat} hours")
    else:
        print(f"[Config] Mode: Run once")

    run_check_cycle(checker, args, proxy_types)

    if args.repeat > 0:
        interval_seconds = args.repeat * 3600
        print(f"\n[Scheduler] Next check in {args.repeat} hours")
        print(f"[Web Server] Running on port {args.port}. Press Ctrl+C to stop.")

        try:
            while True:
                time.sleep(interval_seconds)
                run_check_cycle(checker, args, proxy_types)
                print(f"\n[Scheduler] Next check in {args.repeat} hours")
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            sys.exit(0)
    else:
        print(f"\n[Web Server] Running on port {args.port}. Press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n\nShutting down...")
            sys.exit(0)


if __name__ == "__main__":
    main()