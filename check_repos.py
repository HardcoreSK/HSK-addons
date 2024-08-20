import os
import logging
import xml.etree.ElementTree as ET
import re
from github import Github
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
import hashlib

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Set your GitHub token and repository details here
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
REPO_NAME = 'HardcoreSK/HSK-addons'  # Format: 'owner/repo'
REPOS_FILE_PATH = 'repos'
OUTPUT_FILE_PATH = 'addons_list.xml'

# Initialize GitHub client and repository
g = Github(GITHUB_TOKEN)
repo = g.get_repo(REPO_NAME)

def extract_owner_repo(url):
    path = urlparse(url).path.strip('/')
    return path.split('/', 1)

def get_repositories_from_file():
    try:
        file_content = repo.get_contents(REPOS_FILE_PATH).decoded_content.decode()
        repos = [line.strip() for line in file_content.splitlines() if line.strip()]
        return [extract_owner_repo(url) for url in repos]
    except Exception as e:
        logger.error(f"Error fetching {REPOS_FILE_PATH}: {e}")
        return []

def get_repo_url(owner, repo_name):
    return f"https://github.com/{owner}/{repo_name}"

def search_about_folder_and_extract_info(repo, owner, repo_name, path='/', level=0):
    if level > 3:
        return None
    about_info = []
    try:
        contents = repo.get_contents(path)
        for content in contents:
            if content.type == 'dir':
                # Check for 'about' folder case-insensitively
                if content.name.lower() == 'about':
                    about_folder_path = content.path
                    about_xml_path = find_about_xml(repo, about_folder_path)
                    if about_xml_path:
                        file_content = repo.get_contents(about_xml_path).decoded_content.decode()
                        name, description, package_id, supported_versions = extract_info_from_xml(file_content)
                    else:
                        name, description, package_id, supported_versions = 'N/A', 'N/A', 'N/A', 'N/A'
                    preview_image = find_preview_image(repo, about_folder_path)
                    mod_root_path = '/'.join(about_folder_path.split('/')[:-1])
                    about_info.append((repo.id, owner, repo_name, mod_root_path, name, description, package_id, supported_versions, preview_image))
                # Recursively search in subdirectories
                subdir_about_info = search_about_folder_and_extract_info(repo, owner, repo_name, content.path, level + 1)
                if subdir_about_info:
                    about_info.extend(subdir_about_info)
    except Exception as e:
        logger.error(f"Error accessing repository {repo.full_name}: {e}")
    return about_info

def extract_info_from_xml(content):
    try:
        root = ET.fromstring(content)
        name = root.find('name').text if root.find('name') is not None else 'N/A'
        description = root.find('description').text if root.find('description') is not None else 'N/A'
        package_id = root.find('packageId').text if root.find('packageId') is not None else 'N/A'
        supported_versions = [li.text for li in root.findall('supportedVersions/li')] if root.find('supportedVersions') is not None else []
        return name, description, package_id, supported_versions
    except ET.ParseError as e:
        logger.error(f"Error parsing XML content: {e}")
        return 'N/A', 'N/A', 'N/A', []

def find_preview_image(repo, about_folder_path):
    try:
        contents = repo.get_contents(about_folder_path)
        for content in contents:
            if content.type == 'file' and re.match(r'^preview.*\.(png|jpeg)$', content.name.lower()):
                return f"{content.path}"
    except Exception as e:
        logger.error(f"Error finding preview image in {about_folder_path}: {e}")
    return 'N/A'

def find_about_xml(repo, about_folder_path):
    try:
        contents = repo.get_contents(about_folder_path)
        for content in contents:
            if content.type == 'file' and content.name.lower() == 'about.xml':
                return content.path
    except Exception as e:
        logger.error(f"Error finding about.xml in {about_folder_path}: {e}")
    return None

def generate_xml_string(info_list):
    root = ET.Element('repositories')

    for repo_id, owner, repo_name, mod_root_path, name, description, package_id, supported_versions, preview_image in info_list:
        repo_element = ET.SubElement(root, 'repository')
        ET.SubElement(repo_element, 'repo_id').text = str(repo_id)
        ET.SubElement(repo_element, 'owner').text = owner
        ET.SubElement(repo_element, 'repo_name').text = repo_name
        ET.SubElement(repo_element, 'mod_root_path').text = mod_root_path
        ET.SubElement(repo_element, 'name').text = name
        ET.SubElement(repo_element, 'description').text = description
        ET.SubElement(repo_element, 'package_id').text = package_id
        supported_versions_element = ET.SubElement(repo_element, 'supported_versions')
        for version in supported_versions:
            ET.SubElement(supported_versions_element, 'version').text = version
        ET.SubElement(repo_element, 'preview_image').text = preview_image

    return ET.tostring(root, encoding='utf-8', xml_declaration=True)

def write_paths_to_xml(info_list):
    xml_str = generate_xml_string(info_list)
    new_hash = hashlib.md5(xml_str).hexdigest()

    try:
        existing_file = repo.get_contents(OUTPUT_FILE_PATH)
        existing_content = existing_file.decoded_content
        existing_hash = hashlib.md5(existing_content).hexdigest()

        if new_hash != existing_hash:
            repo.update_file(existing_file.path, "Update about folders paths", xml_str.decode('utf-8'), existing_file.sha)
            logger.info(f"Updated {OUTPUT_FILE_PATH} with new changes.")
        else:
            logger.info(f"No changes detected in {OUTPUT_FILE_PATH}. No update necessary.")
    except Exception as e:
        repo.create_file(OUTPUT_FILE_PATH, "Create about folders paths", xml_str.decode('utf-8'))
        logger.info(f"Created {OUTPUT_FILE_PATH} with new content.")

def find_about_info_parallel(repos):
    all_about_info = []
    with ThreadPoolExecutor(max_workers=5) as executor:
        future_to_repo = {executor.submit(search_about_folder_and_extract_info, g.get_repo(f"{owner}/{repo_name}"), owner, repo_name): (owner, repo_name) for owner, repo_name in repos}
        for future in as_completed(future_to_repo):
            owner, repo_name = future_to_repo[future]
            try:
                about_info = future.result()
                if about_info:
                    all_about_info.extend(about_info)
            except Exception as e:
                logger.error(f"Error processing repository {owner}/{repo_name}: {e}")
    return all_about_info

def main():
    repos = get_repositories_from_file()
    if repos:
        about_info = find_about_info_parallel(repos)
        if about_info:
            write_paths_to_xml(about_info)

if __name__ == "__main__":
    main()
