import socket

def check_dns():
    domains = ["oauth2.googleapis.com", "generativelanguage.googleapis.com", "google.com"]
    for domain in domains:
        try:
            ip = socket.gethostbyname(domain)
            print(f"OK: {domain} resolved to {ip}")
        except socket.gaierror as e:
            print(f"FAIL: {domain} resolution failed: {e}")

if __name__ == "__main__":
    check_dns()
