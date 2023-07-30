import re
import argparse
import random
import socket
import threading
from users import *
import ssl
from statistics import Statistics
import os
import time
from threading import BoundedSemaphore
from collections import defaultdict
import logging

"""
Bugs are just errors in the code. It's the programmers that put them there, intentionally or 
not. And they're often easy to correct - you just have to rewrite a few lines. But here's the 
thing about errors. Even though we caused them, we have a hard time seeing them, even 
when they're right in front of our face. It could be because you don't expect them to be there,
or you're too lazy to go back and fix it. But it's probably because you're just not looking at it right.
Or maybe you caused the error and you're just too blind to see it. We keep running from the mistakes we've made,
so they keep following us. It's a vicious cycle. You can't escape it, so might as well face it.

-Elliot Alderson
"""


# Server class
class Server(object):

    # Constructor
    def __init__(self):
        # Create argument parser
        parser = argparse.ArgumentParser()

        # Add arguments for IP address, port, key file, and certificate file
        parser.add_argument("-i", "--ip", help="Server IP address", default="127.0.0.1")
        parser.add_argument("-p", "--port", help="Server port", type=int, default=8042)
        parser.add_argument("-k", "--key", help="Key file for SSL", default=None)
        parser.add_argument("-c", "--cert", help="Certificate file for SSL", default=None)

        # Parse the arguments
        self.args = parser.parse_args()

        logging.basicConfig(filename='server_logs.log', level=logging.INFO, format='%(asctime)s %(message)s',
                            datefmt='%d/%m/%Y %I:%M:%S %p')

        self.PATTERNS = {
            "SQL Injection": [r"(?i)('.+--|\b(ALTER|CREATE|DELETE|DROP|RENAME|INSERT|SELECT|UPDATE|UNION|WHERE)\b)",
                              re.I],
            "Cross-Site Scripting": [r"(?i)(<\b(script|img|div|table|iframe)\b.*?>)", re.I],
            "XPath Injection": [r"(?i)('|\)|\(|=|' or ')", re.I],
            "Buffer Overflow": [r"(?i)(%s|%d|%n|%x|%o|%p|%u|%ld|%lu|%lx|%lo|%hu|%hx|%ho|%Lf|%Lf|%Lf|%Lf|%Lf|%Lf)",
                                re.I],
            "Format String Attack": [
                r"(?i)(%[^ ]*[sdioxXufFeEgGaAcsCSpnm]|%\.{0,}[0-9]{0,}[^ ]*[sdioxXufFeEgGaAcsCSpnm])", re.I],
            "CRLF Injection": [r"(%0D|%0A|%0D%0A|%0d|%0a|%0d%0a|\r|\n|\r\n)", re.I]
        }

        # Initialize other instance variables
        self.count = 0
        self.running = True
        self._last_request = defaultdict(int)
        self._request_count = defaultdict(int)
        self._delay_times = defaultdict(int)
        self._active_client_count = 0
        self._client_count_lock = threading.Lock()
        self._rate_limit_window = 60
        self._max_requests_per_window = 1000
        self._max_threads = 100
        self._active_threads = BoundedSemaphore(self._max_threads)
        self.userDb = User()
        self.spoofed_ips = ["192.168.1.2", "192.168.1.3"]
        self.current_sentence = None
        self.statisticsDB = Statistics()
        self.sessions = {}
        self.clients = []
        self.client_threads = []


        # Set the default key and certificate files if they are not provided
        if self.args.key is None:
            self.args.key = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'server.key')
        if self.args.cert is None:
            self.args.cert = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'server.crt')

    @staticmethod
    def send_message(client_socket, message):
        message = message.encode('utf-8')
        message_length = len(message)
        send_length = message_length.to_bytes(4, 'big')
        try:
            client_socket.send(send_length)
            client_socket.send(message)
        except ssl.SSLError as e:
            print(f"SSL error occurred: {e}")
        except Exception as e:
            print(f"An error occurred: {e}")

    @staticmethod
    def receive_message(client_socket):
        message_length_header = client_socket.recv(4)
        if not message_length_header:
            return ''
        try:
            message_length = int.from_bytes(message_length_header, 'big')
        except ValueError:
            print(f"Error: Received invalid message length header: '{message_length_header}'")
            return ''
        message = b''
        bytes_received = 0
        while bytes_received < message_length:
            bytes_to_receive = min(1024, message_length - bytes_received)
            chunk = client_socket.recv(bytes_to_receive)
            if not chunk:
                break
            message += chunk
            bytes_received += len(chunk)
        return message.decode('utf-8')

    def start(self):
        try:
            print("""
███████╗███████╗██████╗ ██╗   ██╗███████╗██████╗     ███╗ ██████╗ ███╗   ██╗███╗
██╔════╝██╔════╝██╔══██╗██║   ██║██╔════╝██╔══██╗    ██╔╝██╔═══██╗████╗  ██║╚██║
███████╗█████╗  ██████╔╝██║   ██║█████╗  ██████╔╝    ██║ ██║   ██║██╔██╗ ██║ ██║
╚════██║██╔══╝  ██╔══██╗╚██╗ ██╔╝██╔══╝  ██╔══██╗    ██║ ██║   ██║██║╚██╗██║ ██║
███████║███████╗██║  ██║ ╚████╔╝ ███████╗██║  ██║    ███╗╚██████╔╝██║ ╚████║███║
╚══════╝╚══════╝╚═╝  ╚═╝  ╚═══╝  ╚══════╝╚═╝  ╚═╝    ╚══╝ ╚═════╝ ╚═╝  ╚═══╝╚══╝               
            """)
            print('server starting up on ip %s port %s' % (self.args.ip, self.args.port))
            try:
                self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self.sock.bind((self.args.ip, self.args.port))
            except OSError as e:
                print(f"Failed to bind to the given IP and port: {e}")
                if self.sock:
                    self.sock.close()
                raise
            print("About to start server")
            self.sock.listen(3)
            context = None
            if self.args.key and self.args.cert:
                context = ssl.SSLContext(ssl.PROTOCOL_TLSv1_2)
                context.load_cert_chain(certfile=self.args.cert, keyfile=self.args.key)

            while True:
                print('waiting for a new client')
                client_socket, client_addresses = self.sock.accept()
                if client_addresses[0] in self.spoofed_ips:
                    print("Connection attempt from spoofed IP detected, closing connection.")
                    client_socket.close()
                    continue
                if self.args.key and self.args.cert:
                    try:
                        client_socket = context.wrap_socket(client_socket, server_side=True)
                        if hasattr(client_socket, 'context') and client_socket.context:
                            print("Socket wrapped with SSL")
                        else:
                            print("Error wrapping socket with SSL")
                            raise ssl.SSLError('Unable to wrap the socket with SSL')
                    except ssl.SSLError as e:
                        print("SSL error occurred:", e)
                        client_socket.close()
                        continue
                    except ssl.CertificateError as e:
                        print("Certificate error occurred:", e)
                        client_socket.close()
                        continue

                self.clients.append(client_socket)

                with self._client_count_lock:
                    self._active_client_count += 1
                    if self._active_client_count > 0:
                        self._max_requests_per_window = self._max_requests_per_window // self._active_client_count
                    else:
                        self._max_requests_per_window = self._max_requests_per_window

                current_time = time.time()
                if current_time - self._last_request[client_addresses[0]] < self._rate_limit_window:
                    self._request_count[client_addresses[0]] += 1
                else:
                    self._request_count[client_addresses[0]] = 1
                    self._delay_times[client_addresses[0]] = 0
                self._last_request[client_addresses[0]] = int(current_time)

                if self._request_count[client_addresses[0]] > self._max_requests_per_window:
                    print('client exceeded rate limit')
                    self._delay_times[client_addresses[0]] += 5
                    time.sleep(self._delay_times[client_addresses[0]])
                    with self._client_count_lock:
                        self._active_client_count -= 1
                    continue

                if not self._active_threads.acquire(blocking=False):
                    print('maximum number of active threads reached')
                    client_socket.close()
                    continue
                print('new client entered')
                self.count += 1
                print(self.count)
                client_thread = threading.Thread(target=self.handle_client_connection, args=(client_socket,))
                client_thread.start()
                self.client_threads.append(client_thread)

        except socket.error as e:
            print("Socket error occurred:", e)
        except Exception as e:
            print("An unexpected error occurred:", e)
            if self.sock:
                self.sock.close()


    def handle_client_connection(self, client_socket):
        current_user = None
        self.send_message(client_socket, 'Hello this is server')
        while self.running:
            try:
                server_data = self.receive_message(client_socket)
                if not server_data:
                    raise ConnectionResetError('Client sent an empty message')

                for pattern_name, pattern in self.PATTERNS.items():
                    if re.search(pattern[0], server_data, flags=pattern[1]):
                        print(f"Suspicious pattern detected ({pattern_name})")
                        logging.warning(
                            f"Suspicious pattern ({pattern_name}) detected in message from client {client_socket}")
                arr = server_data.split(",")
                print(server_data)
                if arr is not None and arr[0] == "register" and len(arr) == 4:
                    logging.info(f"Register attempt by client: {client_socket}")
                    print("register user")
                    print(arr)
                    server_data = self.userDb.insert_user(arr[1], arr[2], arr[3])
                    print("server data:", server_data)
                    if server_data:
                        self.send_message(client_socket, "success register")
                    else:
                        self.send_message(client_socket, "failed register")

                elif arr is not None and arr[0] == "login" and len(arr) == 3:
                    print("Login")
                    if arr[1] in self.sessions:
                        print("User already logged in")
                        self.send_message(client_socket, "User is already logged in")
                    else:
                        user_exists = self.userDb.login(arr[1], arr[2])
                        if arr[1] == "admin" or arr[1] == "Admin":
                            logging.warning(f"Admin login attempt by client: {client_socket}")
                        if not user_exists:
                            print("user not found")
                            self.send_message(client_socket, "False")
                        else:
                            print("user found")
                            self.send_message(client_socket, "True")
                            self.sessions[arr[1]] = client_socket
                            current_user = arr[1]
                elif arr is not None and arr[0] == "play_game":
                    print("Playing game")
                    self.sentences = ['Passwords should be kept secure', 'Protect your data from hackers',
                                      'Malware can harm your computer',
                                      'Dont click suspicious email links', 'Use strong passwords always',
                                      'Use two factor authentication']
                    self.current_sentence = random.choice(self.sentences)
                    encrypted_sentence = ''
                    for char in self.current_sentence:
                        if char.isalpha():
                            shifted_char = chr((ord(char.lower()) - 97 + 3) % 26 + 97)
                            encrypted_sentence += shifted_char.upper() if char.isupper() else shifted_char
                        else:
                            encrypted_sentence += char
                    encrypted_sentence = encrypted_sentence.strip()
                    self.send_message(client_socket, encrypted_sentence)

                elif arr is not None and arr[0] == "check_answer" and len(arr) == 3:
                    print("Checking answer")
                    user_input = arr[1]
                    if user_input.lower() == self.current_sentence.lower():
                        self.send_message(client_socket, "successfully decrypted")
                    else:
                        self.send_message(client_socket, "Game over")

                elif arr is not None and arr[0] == "get_statistics" and len(arr) == 2:
                    print("Getting statistics")
                    user_id = arr[1]
                    avg_statistics = self.statisticsDB.get_average_statistics(user_id)
                    user_number_of_games = self.statisticsDB.get_user_number_of_games(user_id)
                    user_wpm_history = self.statisticsDB.get_user_wpm_history(user_id)
                    user_accuracy_history = self.statisticsDB.get_user_Accuracy_history(user_id)

                    statistics_data = (avg_statistics, user_number_of_games, user_wpm_history, user_accuracy_history)
                    self.send_message(client_socket, str(statistics_data))


                elif arr is not None and arr[0] == "get_current_user_id" and len(arr) == 1:
                    print("Getting current user ID")
                    current_user = self.userDb.get_current_user()
                    if current_user:
                        conn = sqlite3.connect('users_new.db')
                        cursor = conn.cursor()
                        query = "SELECT Id FROM Users WHERE email = ?"
                        cursor.execute(query, (current_user,))
                        row = cursor.fetchone()
                        if row:
                            user_id = row[0]
                            self.send_message(client_socket, str(user_id))
                        else:
                            self.send_message(client_socket, "")
                        cursor.close()
                        conn.close()

                elif arr is not None and arr[0] == "get_username":
                    print("Getting username")
                    current_user = self.userDb.get_current_user()
                    if current_user:
                        conn = sqlite3.connect('users_new.db')
                        cursor = conn.cursor()
                        query = "SELECT username FROM Users WHERE email = ?"
                        cursor.execute(query, (current_user,))
                        row = cursor.fetchone()
                        if row:
                            display_username = row[0]
                            self.send_message(client_socket, display_username)
                        else:
                            self.send_message(client_socket, "")
                        cursor.close()
                        conn.close()


                elif arr is not None and arr[0] == "add_statistics" and len(arr) == 6:
                    print("Adding statistics")
                    user_id = arr[1]
                    wpm = float(arr[2])
                    total_words = int(arr[3])
                    wrong_words = int(arr[4])
                    accuracy = float(arr[5])
                    self.statisticsDB.add_statistics(user_id, wpm, total_words, wrong_words, accuracy)

                    avg_statistics = self.statisticsDB.get_average_statistics(user_id)
                    user_number_of_games = self.statisticsDB.get_user_number_of_games(user_id)
                    user_wpm_history = self.statisticsDB.get_user_wpm_history(user_id)
                    user_accuracy_history = self.statisticsDB.get_user_Accuracy_history(user_id)

                    statistics_data = (avg_statistics, user_number_of_games, user_wpm_history, user_accuracy_history)
                    self.send_message(client_socket, str(statistics_data))


                elif arr is not None and arr[0] == "check_email_exists" and len(arr) == 2:
                    print("Checking if email exists")
                    email = arr[1]
                    email_exists = self.userDb.is_exist_by_email(email)
                    if email_exists:
                        self.send_message(client_socket, "True")
                    else:
                        self.send_message(client_socket, "False")

                elif arr is not None and arr[0] == "logout":
                    print("Logging out")
                    if current_user and current_user in self.sessions:
                        del self.sessions[current_user]
                    self.send_message(client_socket, "Logged out successfully")

                else:
                    print("Invalid command")
                    self.send_message(client_socket, "Invalid command")

            except sqlite3.Error as e:
                print(f"Database error: {e}")
            except(ConnectionResetError, ConnectionAbortedError):
                print("client disconnected")
                self._active_threads.release()
                with self._client_count_lock:
                    self._active_client_count -= 1
                if current_user and current_user in self.sessions:
                    del self.sessions[current_user]
                client_socket.close()
                self.clients.remove(client_socket)
                self.sessions = {k: v for k, v in self.sessions.items() if v != client_socket}
                break

    def stop_server(self):
        self.running = False
        for client in self.clients:
            client.close()
        for thread in self.client_threads:
            thread.join()
        self.sock.close()


if __name__ == '__main__':
    server = Server()
    server_thread = threading.Thread(target=server.start)
    server_thread.start()
    try:
        while True:
            if input() == '6FvSJBw94kG4P3GLETmLAX7Baxz338Ry98fZ2vCJtm6FTVcBRY':
                server.stop_server()
                break
    except KeyboardInterrupt:
        print("Interrupted by user, stopping server...")
        server.stop_server()
