import os
import logging
import xml.etree.ElementTree as ET
import re
from github import Github
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
import hashlib
import requests
from xml.dom import minidom

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger()

# Set your GitHub token and repository details here
GITHUB_TOKEN = os.getenv('GITHUB_TOKEN')
REPO_NAME = 'HardcoreSK/HSK-addons'  # Format: 'owner/repo'
REPOS_FILE_PATH = 'repos'
OUTPUT_FILE_PATH = 'addons_list.xml'
DATA_BRANCH = 'data'

if not GITHUB_TOKEN:
    logger.error("GITHUB_TOKEN environment variable not set.")
    exit(1)

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


# --- Optimized: Use Git Trees API to find all about.xml files in one request ---
def search_about_folder_and_extract_info(repo, owner, repo_name):
    about_info = []
    try:
        # Get default branch
        default_branch = repo.default_branch
        # Get branch SHA
        branch = repo.get_branch(default_branch)
        sha = branch.commit.sha
        # Use Git Trees API
        api_url = f"https://api.github.com/repos/{owner}/{repo_name}/git/trees/{sha}?recursive=1"
        headers = {"Authorization": f"token {GITHUB_TOKEN}"} if GITHUB_TOKEN else {}
        response = requests.get(api_url, headers=headers)
        response.raise_for_status()
        tree = response.json().get('tree', [])
        # Find all about.xml files
        about_xml_paths = [item['path'] for item in tree if item['type'] == 'blob' and item['path'].lower().endswith('about.xml')]
        for about_xml_path in about_xml_paths:
            # About folder is the parent directory
            about_folder_path = '/'.join(about_xml_path.split('/')[:-1])
            mod_root_path = '/'.join(about_folder_path.split('/')[:-1])
            # Get about.xml content
            try:
                file_content = fetch_file_raw(owner, repo_name, default_branch, about_xml_path)
                name, description, package_id, supported_versions, mod_dependencies = extract_info_from_xml(file_content)
            except Exception as e:
                logger.error(f"Error reading/parsing {about_xml_path} in {repo.full_name}: {e}")
                name, description, package_id, supported_versions = 'N/A', 'N/A', 'N/A', []
            # Find preview image in about folder
            preview_image = find_preview_image(repo, about_folder_path)
            about_info.append((repo.id, owner, repo_name, mod_root_path, name, description, package_id, supported_versions, preview_image, mod_dependencies))
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
        mod_dependencies = []
        mod_deps_root = root.find('modDependencies')
        if mod_deps_root is not None:
            for li in mod_deps_root.findall('li'):
                dep = {
                    "packageId": li.find('packageId').text if li.find('packageId') is not None else 'N/A',
                    "displayName": li.find('displayName').text if li.find('displayName') is not None else 'N/A',
                    "steamWorkshopUrl": li.find('steamWorkshopUrl').text if li.find('steamWorkshopUrl') is not None else 'N/A',
                }
                mod_dependencies.append(dep)

        return name, description, package_id, supported_versions, mod_dependencies
    except ET.ParseError as e:
        logger.error(f"Error parsing XML content: {e}")
        return 'N/A', 'N/A', 'N/A', []

def find_preview_image(repo, about_folder_path):
    try:
        contents = repo.get_contents(about_folder_path)
        for content in contents:
            if content.type == 'file' and re.match(r'^preview.*\.(png|jpe?g)$', content.name.lower()):
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
    # Sort by owner, repo_name, mod_root_path for stable output
    sorted_info = sorted(
        info_list,
        key=lambda x: (x[1].lower(), x[2].lower(), x[3].lower())
    )
    root = ET.Element('repositories')

    for repo_id, owner, repo_name, mod_root_path, name, description, package_id, supported_versions, preview_image, mod_dependencies in sorted_info:
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
        if mod_dependencies:
            deps_element = ET.SubElement(repo_element, 'mod_dependencies')
            for dep in mod_dependencies:
                dep_element = ET.SubElement(deps_element, 'dependency')
                ET.SubElement(dep_element, 'package_id').text = dep["packageId"]
                ET.SubElement(dep_element, 'display_name').text = dep["displayName"]
                ET.SubElement(dep_element, 'steam_workshop_url').text = dep["steamWorkshopUrl"]

        ET.SubElement(repo_element, 'preview_image').text = preview_image
        
    rough_string = ET.tostring(root, encoding='utf-8')
    reparsed = minidom.parseString(rough_string)
    return reparsed.toprettyxml(indent="  ", encoding='utf-8')

def write_paths_to_xml(info_list):
    xml_str = generate_xml_string(info_list)
    new_hash = hashlib.md5(xml_str).hexdigest()

    # Get the ref for the data branch
    try:
        ref = repo.get_git_ref(f'heads/{DATA_BRANCH}')
    except Exception:
        # Branch does not exist, create it from default branch
        default_branch = repo.default_branch
        default_branch_ref = repo.get_git_ref(f'heads/{default_branch}')
        repo.create_git_ref(ref=f'refs/heads/{DATA_BRANCH}', sha=default_branch_ref.object.sha)
        ref = repo.get_git_ref(f'heads/{DATA_BRANCH}')

    # Try to get the file from the data branch
    try:
        contents = repo.get_contents(OUTPUT_FILE_PATH, ref=DATA_BRANCH)
        existing_hash = hashlib.md5(contents.decoded_content).hexdigest()
        if new_hash != existing_hash:
            repo.update_file(contents.path, "Update about folders paths", xml_str.decode('utf-8'), contents.sha, branch=DATA_BRANCH)
            logger.info(f"Updated {OUTPUT_FILE_PATH} in {DATA_BRANCH} branch with new changes.")
        else:
            logger.info(f"No changes detected in {OUTPUT_FILE_PATH} in {DATA_BRANCH} branch. No update necessary.")
    except Exception:
        # File does not exist, create it
        repo.create_file(OUTPUT_FILE_PATH, "Create about folders paths", xml_str.decode('utf-8'), branch=DATA_BRANCH)
        logger.info(f"Created {OUTPUT_FILE_PATH} in {DATA_BRANCH} branch with new content.")

def find_about_info_parallel(repos):
    all_about_info = []
    with ThreadPoolExecutor(max_workers=10) as executor:
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
    
def fetch_file_raw(owner, repo_name, branch, path):
    url = f"https://raw.githubusercontent.com/{owner}/{repo_name}/{branch}/{path}"
    resp = requests.get(url)
    resp.raise_for_status()
    return resp.text

def main():
    repos = get_repositories_from_file()
    if repos:
        about_info = find_about_info_parallel(repos)
        if about_info:
            write_paths_to_xml(about_info)

if __name__ == "__main__":
    main()
