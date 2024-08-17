import requests
import os, time
from influxdb_client_3 import InfluxDBClient3, Point

# GitHub API tokens
tokens = os.getenv("TOKENS").split(',')

# InfluxDB credentials
influxdb_url = os.getenv("INFLUXDB_URL")
influxdb_token = os.getenv("INFLUXDB_TOKEN")
influxdb_org = os.getenv("INFLUXDB_ORG")
influxdb_bucket = os.getenv("INFLUXDB_BUCKET")

# Initialize InfluxDB client
client = InfluxDBClient3(host=influxdb_url, token=influxdb_token, org=influxdb_org)

def get_usage(token):
    headers = {
        'Authorization': f'token {token}',
        'Accept': 'application/vnd.github.v3+json'
    }
    response = requests.get('https://api.github.com/rate_limit', headers=headers)
    return response.json()

def send_to_influxdb(metrics, tag):
    point = Point("github_api_usage") \
        .field("core_remaining", metrics['resources']['core']['remaining']) \
        .tag("token", tag)
    client.write(database=influxdb_bucket, record=point)

def main():
    for i, token in enumerate(tokens):
        metrics = get_usage(token)
        send_to_influxdb(metrics, f"token_{i+1}")

if __name__ == '__main__':
    main()
