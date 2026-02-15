import os
from dotenv import load_dotenv
load_dotenv()
url = os.getenv("DATABASE_URL", "EMPTY")
print(f"Length: {len(url)}")
print(f"First 60: {url[:60]}")
if "@" in url:
    host_part = url.split("@")[1].split("/")[0]
    print(f"Host: {host_part}")
else:
    print("No @ in URL")
