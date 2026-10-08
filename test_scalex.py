# test_scalex.py
import requests

url = "http://192.168.201.248/AtherReport/"

print(f"Connecting to {url}...")
try:
    # Most internal SSRS reports need Windows/domain authentication or are open intranet
    response = requests.get(url, timeout=5)
    print(f"Status Code: {response.status_code}")
    print(f"Server Type: {response.headers.get('Server', 'Unknown')}")
    print(f"Content-Type: {response.headers.get('Content-Type', 'Unknown')}")
    
    # Save the first 1000 characters to see if it's HTML or SSRS viewer
    with open("scalex_response_sample.html", "w", encoding="utf-8") as f:
        f.write(response.text)
    print("Saved response snippet to 'scalex_response_sample.html'")

except requests.exceptions.ConnectTimeout:
    print("❌ Connection Timed Out. Make sure you are on the plant intranet/VPN.")
except requests.exceptions.ConnectionError as e:
    print(f"❌ Connection Failed: {e}")