from flask import Flask, render_template, request, redirect, url_for, flash
import requests
from bs4 import BeautifulSoup
import json
import os
import difflib
import schedule
from datetime import datetime
import logging
import time
import dns.resolver
import urllib.parse
import asyncio
import aiohttp
from werkzeug.utils import secure_filename

app = Flask(__name__)

@app.route('/about')
def about():
    return render_template('about.html')
@app.route('/doc')
def doc():
    return render_template('doc.html')


UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'txt'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Store monitored websites in a dictionary with website names as keys
monitored_websites = {}

# Configure logging to a file
logging.basicConfig(filename='app.log', level=logging.DEBUG,
                    format='%(asctime)s [%(levelname)s] %(message)s', datefmt='%Y-%m-%d %H:%M:%S')

# Log the start of the application
logging.info("Starting the application")

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def read_websites_from_txt(file_path):
    try:
        with open(file_path, 'r') as file:
            websites = [line.strip() for line in file.readlines() if line.strip()]
        return websites
    except Exception as e:
        logging.error("Error reading websites from file: %s", str(e))
        return []

def add_websites_from_txt(file_path):
    websites = read_websites_from_txt(file_path)
    for website_url in websites:
        add_and_create_baseline(website_url)

def check_website_alive(url):
    try:
        response = requests.get(url, timeout=10)
        return response.status_code == 200
    except requests.RequestException as e:
        logging.error("Error checking website status for URL %s: %s", url, str(e))
        return False

def fetch_and_create_baseline(url):
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            textual_content = soup.get_text()
            dom_tree = soup.prettify()
            content_length = len(response.text)
            current_info = {
            "textual_content": textual_content,
            "dom_tree": dom_tree,
            "content_length": content_length,
            "current_ip": monitored_websites.get(url, {}).get('current_ip', 'Not Available')  # Add current IP to baseline
            }
            create_baseline(url, current_info)
            return current_info
        else:
            logging.warning("Failed to fetch and create baseline for URL %s. HTTP status code: %d", url, response.status_code)
            return None
    except requests.RequestException as e:
        logging.error("Error fetching and creating baseline for URL %s: %s", url, str(e))
        return None

def create_baseline(url, info):
    domain_name = url.split('//')[-1].split('/')[0].replace('.', '_')
    json_file_path = os.path.join('baseline', f'{domain_name}_baseline.json')
    if os.path.exists(json_file_path):
        logging.info("Baseline already exists for URL %s. Skipping creation.", url)
    else:
        with open(json_file_path, 'w', encoding='utf-8') as json_file:
            json.dump(info, json_file, ensure_ascii=False, indent=4)
        logging.info("Baseline created for URL %s", url)



def add_and_create_baseline(new_url):
    website_name = new_url.split('//')[-1].split('/')[0]

    try:
        if website_name not in monitored_websites:
            monitored_websites[website_name] = {'url': new_url, 'status': 'Alive'}

            # Check if a baseline already exists for the website
            if 'baseline' not in monitored_websites[website_name]:
                current_info = fetch_and_create_baseline(new_url)
                if current_info:
                    monitored_websites[website_name]['baseline'] = current_info
                    # Add the current IP to the baseline
                    monitored_websites[website_name]['baseline']['current_ip'] = fetch_ip_address(new_url)
                else:
                    # If baseline creation fails, remove the website from the monitored list
                    del monitored_websites[website_name]

        # Add DNS information to the baseline
        monitored_websites[website_name]['baseline']['current_ip'] = fetch_ip_address(new_url)

    except Exception as e:
        logging.error("Error adding and creating baseline for URL %s: %s", new_url, str(e))




def compare_with_baseline(url, current_info, baseline_info):
    textual_content_changed = current_info['textual_content'] != baseline_info['textual_content']
    dom_changed = current_info['dom_tree'] != baseline_info['dom_tree']
    content_length_changed = current_info['content_length'] != baseline_info['content_length']
    changes = []
    if textual_content_changed:
        changes.append("Textual content has changed.")
        changes.extend(list(difflib.unified_diff(baseline_info['textual_content'].splitlines(), current_info['textual_content'].splitlines())))
    if dom_changed:
        changes.append("DOM structure has changed.")
        changes.extend(list(difflib.unified_diff(baseline_info['dom_tree'].splitlines(), current_info['dom_tree'].splitlines())))
    if content_length_changed:
        changes.append("Content length has changed.")
    return changes

def check_website_statuses():
    for website_name, website_info in monitored_websites.items():
        url = website_info['url']
        if check_website_alive(url):
            website_status = "Alive"
        else:
            website_status = "Down"
        monitored_websites[website_name]['status'] = website_status
        monitored_websites[website_name]['last_checked'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        current_info = fetch_website_info(url)
        baseline_info = monitored_websites[website_name].get('baseline')
        if current_info:
            website_status = "Alive" if check_website_alive(url) else "Down"
            changes = compare_with_baseline(url, current_info, baseline_info)
            monitored_websites[website_name]['changes'] = changes
        else:
            website_status = "Down"
            changes = []
        logging.info("Website %s status: %s", website_name, website_status)
        logging.info("Website %s IP: %s", website_name, monitored_websites[website_name]['baseline']['current_ip'])

        # Update website_info and website_status directly in the monitored_websites dictionary
        monitored_websites[website_name]['website_info'] = current_info
        monitored_websites[website_name]['website_status'] = website_status

        # Fetch and update DNS records
        monitored_websites[website_name]['current_ip'] = fetch_ip_address(url)


def fetch_website_info(url):
    try:
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            textual_content = soup.get_text()
            dom_tree = soup.prettify()
            content_length = len(response.text)
            return {
                "textual_content": textual_content,
                "dom_tree": dom_tree,
                "content_length": content_length
            }
        else:
            logging.warning("Failed to fetch website info for URL %s. HTTP status code: %d", url, response.status_code)
            return None
    except requests.RequestException as e:
        logging.error("Error fetching website info for URL %s: %s", url, str(e))
        return None
    

#fetch ip
        
def fetch_ip_address(url):
    try:
        # Extract domain name from the URL
        domain_name = urllib.parse.urlparse(url).netloc
        result = dns.resolver.resolve(domain_name, 'A')
        return result[0].address
    except dns.resolver.NXDOMAIN:
        logging.error(f"DNS record not found for {url}")
        return 'Not Available'
    except Exception as e:
        logging.error(f"Error querying DNS for {url}: {str(e)}")
        return 'Not Available'
    
# Check DNS records
def check_dns_records():
    for website_name, website_info in monitored_websites.items():
        url = website_info['url']
        current_ip = fetch_ip_address(url)

        if 'current_ip' in website_info and website_info['current_ip'] != current_ip:
            logging.warning(f"DNS record changed for {website_name}. Old IP: {website_info['current_ip']}, New IP: {current_ip}")
        monitored_websites[website_name]['current_ip'] = current_ip

        # Check against the baseline
        baseline_ip = website_info.get('baseline', {}).get('current_ip')
        if baseline_ip and baseline_ip != current_ip:
            logging.warning(f"DNS record changed from baseline for {website_name}. Baseline IP: {baseline_ip}, Current IP: {current_ip}")




            
schedule.every(60).seconds.do(check_website_statuses)
schedule.every(60).seconds.do(check_dns_records)


#Root route
@app.route("/")
def index():
    return redirect(url_for('dashboard'))

#Adding websites to be monitored section 
@app.route("/add_website", methods=["POST"])
def add_website():
    new_url = request.form.get("new_url")
    file = request.files.get("file")

    if new_url is not None and new_url.strip():
        add_and_create_baseline(new_url)
        flash('Website added successfully', 'success')

    elif file and allowed_file(file.filename):
        file_content = file.read().decode("utf-8")
        websites_from_file = file_content.splitlines()

        for website_url in websites_from_file:
            add_and_create_baseline(website_url)

        flash('File uploaded successfully', 'success')
    elif file and not allowed_file(file.filename):
        flash('Invalid file format. Please upload a file with a .txt extension', 'error')
    else:
        flash('No valid input provided. Please enter a URL or upload a file', 'error')

    return redirect(url_for('dashboard'))

# Dashboard route
@app.route("/dashboard", methods=["GET", "POST"])
def dashboard():
    if request.method == 'POST':
        if 'file' not in request.files:
            flash('No file part', 'error')
            return redirect(request.url)

        file = request.files['file']

        if file.filename == '':
            flash('No selected file', 'error')
            return redirect(request.url)

        if file and allowed_file(file.filename):
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], file.filename)
            file.save(file_path)

            add_websites_from_txt(file_path)

            flash('File uploaded successfully', 'success')
            return redirect(url_for('dashboard'))

    # Update DNS records
    check_dns_records()

    check_website_statuses()
    return render_template("dashboard.html", monitored_websites=monitored_websites,)


# Monitoring route
@app.route("/monitor/<website_name>")
def monitor_website(website_name):
    if website_name in monitored_websites:
        url = monitored_websites[website_name]['url']
        current_info = fetch_website_info(url)
        baseline_info = monitored_websites[website_name].get('baseline')
        if current_info:
            website_status = "Alive" if check_website_alive(url) else "Down"
            changes = compare_with_baseline(url, current_info, baseline_info)
            # Correctly access the IP address from monitored_websites dictionary
            current_ip = monitored_websites[website_name]['current_ip']
        else:
            current_info = {"textual_content": "Unable to fetch this website.", "dom_tree": "", "content_length": ""}
            website_status = "Down"
            changes = []
            current_ip = 'Not Available'

        logging.info("Monitoring specific website: %s", website_name)
        return render_template("monitor_specific.html", website_info=current_info, website_status=website_status, changes=changes, current_ip=current_ip, website_name=website_name)

    return redirect(url_for('dashboard'))


######################################
statuses = {
    200: "200 OK Website Available 👍",
    301: "301 Permanent Redirect ⏳",
    302: "302 Temporary Redirect ⏳",
    404: "404 Not Found 😔",
    500: "500 Internal Server Error 😞",
    503: "503 Service Unavailable 😞"
}
########################################
async def check_website(session, url):
    try:
        start_time = time.time()
        async with session.get(url) as response:
            status = statuses.get(response.status, "Unknown Status ❌")
            elapsed_time = time.time() - start_time
            logging.info("Checked %s, Status: %s, Elapsed Time: %s seconds", url, status, elapsed_time)
            return url, status, elapsed_time
    except aiohttp.ClientError:
        logging.error("Failed to check %s", url)
        return url, "Failed to respond", None
#######################################
@app.route('/uptime', methods=['GET', 'POST'])
def uptime():
    website_urls = []

    if request.method == 'POST':
        uploaded_file = request.files.get('file')
        single_url = request.form.get('single_url')

        if uploaded_file:
            if uploaded_file.filename.lower().endswith('.txt'):
                filename = secure_filename(uploaded_file.filename)
                lines = uploaded_file.read().decode("utf-8").splitlines()
                website_urls.extend(lines)
            else:
                return render_template('uptime_error.html', error_message='Invalid file format. Please upload a text file.')
        elif single_url:
            website_urls.append(single_url)
        else:
            for i in range(int(request.form.get('num_websites', 0))):
                url = request.form.get(f"url_{i+1}")
                website_urls.append(url)

        async def main():
            async with aiohttp.ClientSession() as session:
                tasks = [check_website(session, url) for url in website_urls]
                results = await asyncio.gather(*tasks)

            return results

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        result = loop.run_until_complete(main())
        return render_template('uptime_results.html', results=result)

    return render_template('uptime.html')



if __name__ == "__main__":
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    app.secret_key = '000-000'  # Change this to a random secure key in production

    # Start the Flask development server in a separate thread without the reloader
    import threading
    flask_thread = threading.Thread(target=app.run, kwargs={'debug': True, 'use_reloader': False, 'port': 8000})
    flask_thread.start()

    # Call check_dns_records once at the beginning
    check_dns_records()

  # With the following:
asyncio.set_event_loop(asyncio.new_event_loop())
loop = asyncio.get_event_loop()

try:
    while True:
        schedule.run_pending()
        loop.run_until_complete(asyncio.sleep(1))
except KeyboardInterrupt:
    pass
finally:
    loop.close()