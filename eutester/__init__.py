#!/usr/bin/python
# -*- coding: utf-8 -*-
# Software License Agreement (BSD License)
#
# Copyright (c) 2009-2011, Eucalyptus Systems, Inc.
# All rights reserved.
#
# Redistribution and use of this software in source and binary forms, with or
# without modification, are permitted provided that the following conditions
# are met:
#
#   Redistributions of source code must retain the above
#   copyright notice, this list of conditions and the
#   following disclaimer.
#
#   Redistributions in binary form must reproduce the above
#   copyright notice, this list of conditions and the
#   following disclaimer in the documentation and/or other
#   materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# Author: vic.iglesias@eucalyptus.com


__version__ = '0.0.1'

import re
import os
import subprocess
import threading
import paramiko
import select
import boto
import random
import time
import signal
import copy 
from threading import Thread

from boto.ec2.regioninfo import RegionInfo
from boto.s3.connection import OrdinaryCallingFormat

from machine import machine
import eulogger
from euservice import EuserviceManager


class TimeoutFunctionException(Exception): 
    """Exception to raise on a timeout""" 
    pass 

class Eutester(object):
    def __init__(self, config_file=None, password=None, keypath=None, credpath=None, aws_access_key_id=None, aws_secret_access_key = None, account="eucalyptus",  user="admin", boto_debug=0):
        """  
        EUCADIR => $eucadir, 
        VERIFY_LEVEL => $verify_level, 
        TOOLKIT => $toolkit, 
        DELAY => $delay, 
        FAIL_COUNT => $fail_count, 
        INPUT_FILE => $input_file, 
        PASSWORD => $password }
        , credpath=None, timeout=30, exit_on_fail=0
        EucaTester takes 2 arguments to their constructor
        1. Configuration file to use
        2. Eucalyptus component to connect to [CLC NC00 SC WS CC00] or a hostname
        3. Password to connect to the host
        4. 
        """
        
        ### Default values for configuration
        self.config_file = config_file 
        self.eucapath = "/opt/eucalyptus"
        self.current_ssh = "clc"
        self.boto_debug = boto_debug
        self.ssh = None
        self.sftp = None
        self.password = password
        self.keypath = keypath
        self.credpath = credpath
        self.timeout = 30
        self.delay = 0
        self.exit_on_fail = 0
        self.fail_count = 0
        self.start_time = time.time()
        self.key_dir = "./"
        self.account_id = 0000000000001
        self.hypervisor = None
        
        ##### Euca Logs 
        self.cloud_log_buffer = ''
        self.cc_log_buffer  = ''
        self.nc_log_buffer = ''
        self.sc_log_buffer = ''
        self.walrus_log_buffer = ''
        self.logging_thread = False
        
        ### Eutester logs
        self.logger = eulogger.Eulogger(identifier="localhost")
        self.debug = self.logger.log.debug
        self.critical = self.logger.log.critical
        self.info = self.logger.log.info
        self.logging_thread_pool = []
        ### LOGS to keep for printing later
        self.fail_log = []
        self.running_log = self.logger.log
        
        ### SSH Channels for tailing log files
        self.cloud_log_channel = None
        self.cc_log_channel= None
        self.nc_log_channel= None
        
        self.clc_index = 0

        ### If I have a config file
        ### PRIVATE CLOUD
        if self.config_file != None:
            ## read in the config file
            self.debug("Reading config file: " + config_file)
            self.config = self.read_config(config_file)
            ### Set the eucapath
            if "REPO" in self.config["machines"][0].source:
                self.eucapath="/"
            #self.hypervisor = self.get_hypervisor()
            ### No credpath but does have password and an ssh connection to the CLC
            ### Private cloud with root access 
            ### Need to get credentials for the user if there arent any passed in
            ### Need to create service manager for user if we have an ssh connection and password
            if (self.password != None):
                self.clc = self.get_component_machines("clc")[self.clc_index]
                if self.credpath is None:
                    ### TRY TO GET CREDS ON FIRST CLC if it fails try on second listed clc, if that fails weve hit a terminal condition
                    try:
                        self.sftp = self.clc.ssh.connection.open_sftp()
                        self.credpath = self.get_credentials(account,user)
                    except Exception, e:
                        self.swap_clc()
                        self.sftp = self.clc.ssh.connection.open_sftp()
                        self.credpath = self.get_credentials(account,user)
                self.service_manager = EuserviceManager(self)
                self.clc = self.service_manager.get_enabled_clc().machine

        ### Pull the access and secret keys from the eucarc
        if (self.credpath != None):         
            aws_access_key_id = self.get_access_key()
            aws_secret_access_key = self.get_secret_key()
                    
        ### If you have credentials for the boto connections, create them
        if (aws_access_key_id != None) and (aws_secret_access_key != None):
           self.ec2 = boto.connect_ec2(aws_access_key_id=aws_access_key_id,
                                        aws_secret_access_key=aws_secret_access_key,
                                        is_secure=False,
                                        api_version = '2009-11-30',
                                        region=RegionInfo(name="eucalyptus", endpoint=self.get_clc_ip()),
                                        port=8773,
                                        path="/services/Eucalyptus",
                                        debug=self.boto_debug)
           self.walrus = boto.connect_s3(aws_access_key_id=aws_access_key_id,
                                          aws_secret_access_key=aws_secret_access_key,
                                          is_secure=False,
                                          host=self.get_walrus_ip(),
                                          port=8773,
                                          path="/services/Walrus",
                                          calling_format=OrdinaryCallingFormat(),
                                          debug=self.boto_debug)
           self.euare = boto.connect_iam(aws_access_key_id=aws_access_key_id,
                                          aws_secret_access_key=aws_secret_access_key,
                                          is_secure=False,
                                          host=self.get_clc_ip(),
                                          port=8773,
                                          path="/services/Euare",
                                          debug=self.boto_debug)
    
    def __del__(self):
        self.logging_thread = False
        
    def swap_clc(self):
        if self.clc_index is 0:
            self.clc = self.get_component_machines("clc")[1]
            self.clc_index = 1
        else:
            self.clc = self.get_component_machines("clc")[0]
            self.clc_index = 0
    
    def read_config(self, filepath):
        """ Parses the config file at filepath returns a dictionary with the config
            Config file
            ----------
            The configuration file for (2) private cloud mode has the following structure:
            
                clc.mydomain.com CENTOS 5.7 64 REPO [CC00 CLC SC00 WS]    
                nc1.mydomain.com VMWARE ESX-4.0 64 REPO [NC00]
            
            Columns
            ------ 
                IP or hostname of machine   
                Distro installed on machine - Options are RHEL, CENTOS, UBUNTU additionally VMWARE can be used for NCs 
                Distro version on machine  - RHEL (5.x, 6.x), CentOS (5.x), UBUNTU (LUCID)
                Distro base architecture  - 32 or 64
                System built from packages (REPO) or source (BZR), packages assumes path to eucalyptus is /, bzr assumes path to eucalyptus is /opt/eucalyptus
                List of components installed on this machine encapsulated in brackets []
            
            These components can be:
            
                CLC - Cloud Controller   
                WS - Walrus   
                SC00 - Storage controller for cluster 00   
                CC00 - Cluster controller for cluster 00    
                NC00 - A node controller in cluster 00   
        """
        config_hash = {}
        machines = []
        f = None
        try:
            f = open(filepath, 'r')
        except IOError as (errno, strerror):
            self.debug( "ERROR: Could not find config file " + self.config_file)
            raise
            
        for line in f:
            ### LOOK for the line that is defining a machine description
            line = line.strip()
            re_machine_line = re.compile(".*\[.*]")
            if re_machine_line.match(line):
                #print "Matched Machine :" + line
                machine_details = line.split(None, 5)
                machine_dict = {}
                machine_dict["hostname"] = machine_details[0]
                machine_dict["distro"] = machine_details[1]
                machine_dict["distro_ver"] = machine_details[2]
                machine_dict["arch"] = machine_details[3]
                machine_dict["source"] = machine_details[4]
                machine_dict["components"] = map(str.lower, machine_details[5].strip('[]').split())
                ### ADD the machine to the array of machine
                cloud_machine = machine(   machine_dict["hostname"], 
                                        machine_dict["distro"], 
                                        machine_dict["distro_ver"], 
                                        machine_dict["arch"], 
                                        machine_dict["source"], 
                                        machine_dict["components"],
                                        self.password,
                                        self.keypath
                                        )
                machines.append(cloud_machine)
            if re.search("network",line, re.IGNORECASE):
                config_hash["network"] = line.split()[1].lower()
        config_hash["machines"] = machines 
        return config_hash
    
    def get_network_mode(self):
        return self.config['network']
    
    def get_hypervisor(self):
        """ Requires that a config file was passed.
            Returns the supported hypervisor that should be used absed on the config file passed into the system
            For RHEL 6 and UBUNTU: kvm
            For CentOS 5: xen
            For VMware: vmware
        """
        ncs = self.get_component_machines("nc00")
        if re.search("vmware", ncs[0].distro, re.IGNORECASE):
            return "vmware"
        elif re.search("rhel", ncs[0].distro, re.IGNORECASE) or re.search("centos", ncs[0].distro, re.IGNORECASE):
            if re.search("6\.", ncs[0].distro_ver, re.IGNORECASE):
                return "kvm"
            if re.search("5\.", ncs[0].distro_ver, re.IGNORECASE):
                return "xen"
        elif re.search("ubuntu", ncs[0].distro, re.IGNORECASE):
            return "kvm" 
        
            
    def get_component_ip(self, component):
        """ Parse the machine list and a bm_machine object for a machine that matches the component passed in"""
        #loop through machines looking for this component type
        component.lower()
        machines_with_role = [machine.hostname for machine in self.config['machines'] if component in machine.components]
        if len(machines_with_role) == 0:
            raise Exception("Could not find component "  + component + " in list of machines")
        else:
             return machines_with_role[0]
    
    def get_machine_by_ip(self, hostname):
         machines = [machine for machine in self.config['machines'] if re.search(hostname, machine.hostname)]
         if len(machines) == 0:
            self.fail("Could not find machine at "  + hostname + " in list of machines")
            return None
         else:
             return machines[0]
         
    def get_component_machines(self, component):
        #loop through machines looking for this component type
        """ Parse the machine list and a list of bm_machine objects that match the component passed in"""
        component.lower()
        machines_with_role = [machine for machine in self.config['machines'] if component in machine.components]
        if len(machines_with_role) == 0:
            raise Exception("Could not find component "  + component + " in list of machines")
        else:
             return machines_with_role

    def swap_component_hostname(self, hostname):
        if hostname != None:
            if len(hostname) < 5:
                component_hostname = self.get_component_ip(hostname)
                hostname = component_hostname
        return hostname
       
    def get_credentials(self, account="eucalyptus", user="admin"):
        """Login to the CLC and download credentials programatically for the user and account passed in
           Defaults to admin@eucalyptus 
        """
        admin_cred_dir = "eucarc-" + account + "-" + user
        
        ### SETUP directory remotely
        self.sys("rm -rf " + admin_cred_dir)
        self.sys("mkdir " + admin_cred_dir)
        
        ### Download credentials from Active CLC
        cmd_download_creds = self.eucapath + "/usr/sbin/euca_conf --get-credentials " + admin_cred_dir + "/creds.zip " + "--cred-user "+ user +" --cred-account " + account 

        if self.found( cmd_download_creds, ""):
            raise IOError("Error downloading credentials")
        if self.found( "unzip -o " + admin_cred_dir + "/creds.zip " + "-d " + admin_cred_dir, "cannot find zipfile directory"):
            raise IOError("Empty ZIP file returned by CLC")
        
        ### SETUP directory locally
        os.system("rm -rf " + admin_cred_dir)
        os.mkdir(admin_cred_dir)
        
        ### DOWNLOAD creds from clc
        self.sftp.get(admin_cred_dir + "/creds.zip" , admin_cred_dir + "/creds.zip")
        os.system("unzip -o " + admin_cred_dir + "/creds.zip -d " + admin_cred_dir )
        return admin_cred_dir
        
    def get_access_key(self):
        """Parse the eucarc for the EC2_ACCESS_KEY"""
        return self.parse_eucarc("EC2_ACCESS_KEY")   
    
    def get_secret_key(self):
       """Parse the eucarc for the EC2_SECRET_KEY"""
       return self.parse_eucarc("EC2_SECRET_KEY")
    
    def get_account_id(self):
        """Parse the eucarc for the EC2_ACCOUNT_NUMBER"""
        return self.parse_eucarc("EC2_ACCOUNT_NUMBER")
        
    def parse_eucarc(self, field):
        with open( self.credpath + "/eucarc") as eucarc:
            for line in eucarc.readlines():
                if re.search(field, line):
                    return line.split("=")[1].strip().strip("'")
            raise Exception("Unable to find account id in eucarc")
    
    def get_walrus_ip(self):
        """Parse the eucarc for the S3_URL"""
        walrus_url = self.parse_eucarc("S3_URL")
        return walrus_url.split("/")[2].split(":")[0]
    
    def get_clc_ip(self):
        """Parse the eucarc for the EC2_URL"""
        ec2_url = self.parse_eucarc("EC2_URL")
        return ec2_url.split("/")[2].split(":")[0]        
        
    def create_ssh(self, hostname, password=None, keypath=None, username="root"):
        """ Returns a paramiko SSHClient object for the hostname provided, either keypath or password must be provided"""
        hostname = self.swap_component_hostname(hostname)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())            
        if keypath == None:
            if password==None:
                password= self.password
            client.connect(hostname, username=username, password=password)
        else:
            client.connect(hostname,  username=username, key_filename=keypath)
        return client
    
    def poll_euca_logs(self):
        self.debug( "Starting to poll Eucalyptus Logs")
        ## START CLOUD Log
        cloud_ssh =  self.create_ssh("clc", password=self.password)          
        self.cloud_log_channel = cloud_ssh.invoke_shell()
        self.cloud_log_channel.send("tail -f "  + self.eucapath + "/var/log/eucalyptus/cloud-output.log \n")
        ### START WALRUS Log    
        walrus_ssh =  self.create_ssh("ws", password=self.password)
        self.walrus_log_channel = walrus_ssh.invoke_shell()
        self.walrus_log_channel.send("tail -f "  + self.eucapath + "/var/log/eucalyptus/cloud-output.log \n")
        ## START CC Log
        cluster_ssh =  self.create_ssh("cc00", password=self.password)
        self.cc_log_channel = cluster_ssh.invoke_shell()
        self.cc_log_channel.send("tail -f "  + self.eucapath + "/var/log/eucalyptus/cc.log \n")
        ## START SC Log
        storage_ssh =  self.create_ssh("sc00", password=self.password)
        self.sc_log_channel = storage_ssh.invoke_shell()
        self.sc_log_channel.send("tail -f "  + self.eucapath + "/var/log/eucalyptus/cloud-output.log \n")
        ## START NC LOG
        nc_ssh =  self.create_ssh("nc00", password=self.password)
        self.nc_log_channel = nc_ssh.invoke_shell()
        self.nc_log_channel.send("tail -f "  + self.eucapath + "/var/log/eucalyptus/nc.log \n")
        self.logging_thread = True
        
        ### Begin polling channel for any new data
        while self.logging_thread:
            ### CLOUD LOG
            rl, wl, xl = select.select([self.cloud_log_channel],[],[],0.0)
            if len(rl) > 0:
                self.cloud_log_buffer += self.cloud_log_channel.recv(1024)
            ### CC LOG
            rl, wl, xl = select.select([self.cc_log_channel],[],[],0.0)
            if len(rl) > 0:
                self.cc_log_buffer += self.cc_log_channel.recv(1024)
            ### SC LOG
            rl, wl, xl = select.select([self.sc_log_channel],[],[],0.0)
            if len(rl) > 0:
                self.sc_log_buffer += self.sc_log_channel.recv(1024)
            ### WALRUS LOG
            rl, wl, xl = select.select([self.walrus_log_channel],[],[],0.0)
            if len(rl) > 0:
                self.walrus_log_buffer += self.walrus_log_channel.recv(1024)
            ### NC LOG
            rl, wl, xl = select.select([self.nc_log_channel],[],[],0.0)
            if len(rl) > 0:
                self.nc_log_buffer += self.nc_log_channel.recv(1024)
            self.sleep(1)
            
    def start_euca_logs(self):
        '''Start thread to poll logs''' 
        thread = threading.Thread(target=self.poll_euca_logs, args=())
        thread.daemon = True
        self.logging_thread_pool.append(thread.start())
        
    def stop_euca_logs(self):
        '''Terminate thread that is polling logs''' 
        self.logging_thread = False
        
    def save_euca_logs(self,prefix="eutester-"):
        '''Save log buffers to a file''' 
        FILE = open( prefix + "clc.log","w")
        FILE.writelines(self.cloud_log_buffer)
        FILE.close()
        FILE = open( prefix + "walrus.log","w")
        FILE.writelines(self.walrus_log_buffer)
        FILE.close()
        FILE = open( prefix + "cc.log","w")
        FILE.writelines(self.cc_log_buffer)
        FILE.close()
        FILE = open( prefix + "sc.log","w")
        FILE.writelines(self.sc_log_buffer)
        FILE.close()
        FILE = open( prefix + "nc.log","w")
        FILE.writelines(self.nc_log_buffer)
        FILE.close()        
                               
    def handle_timeout(self, signum, frame): 
        raise TimeoutFunctionException()
    
    def sys(self, cmd, verbose=True):
        """ By default will run a command on the CLC machine, the connection used can be changed by passing a different hostname into the constructor
            For example:
            instance = Eutester( hostname=instance.ip_address, keypath="my_key.pem")
            instance.sys("mount") # check mount points on instance and return the output as a list
        """
        return self.clc.sys(cmd, verbose=verbose)

    def local(self, cmd):
        """ Run a command locally on the tester"""
        for item in os.popen("ls").readlines():
            if re.match(self.credpath,item):
                cmd = ". " + self.credpath + "/eucarc && " + cmd
        std_out_return = os.popen(cmd).readlines()
        return std_out_return
    
    def found(self, command, regex, local=False):
        """ Returns a Boolean of whether the result of the command contains the regex"""
        if local:
            result = self.local(command)
        else:
            result = self.sys(command)
        for line in result:
            found = re.search(regex,line)
            if found:
                return True
        return False 
    
    def grep(self, string,list):
        """ Remove the strings from the list that do not match the regex string"""
        expr = re.compile(string)
        return filter(expr.search,list)
        
    def diff(self, list1, list2):
        """Return the diff of the two lists"""
        return list(set(list1)-set(list2))
    
    def fail(self, message):
        self.critical("[TEST_REPORT] FAILED: " + message)
        self.fail_log.append(message)
        self.fail_count += 1
        if self.exit_on_fail == 1:
            raise Exception("Test step failed")
        else:
            return 0 
    
    def clear_fail_log(self):
        self.fail_log = []
        return
    
    def get_exectuion_time(self):
        """Returns the total execution time since the instantiation of the Eutester object"""
        return time.time() - self.start_time
       
    def clear_fail_count(self):
        self.fail_count = 0

    def do_exit(self):
        """Prints a short sumary of the test including the failure messages and time to execute. Exits 0 if no failures were encountered or 1 if there were"""
        self.debug( "******************************************************")
        self.debug( "*" + "    Failures:" + str(self.fail_count) )
        for message in self.fail_log:
            self.debug( "*" + "            " + message + "\n")
        self.debug( "*" + "    Time to execute: " + str(self.get_exectuion_time()) )
        self.debug( "******************************************************" )          
        if self.fail_count > 0:
            exit(1)
        else:
            exit(0)
        
    def sleep(self, seconds=1):
        """Convinience function for time.sleep()"""
        time.sleep(seconds)
        
    def __str__(self):
        s  = "+++++++++++++++++++++++++++++++++++++++++++++++++++++\n"
        s += "+" + "Eucateser Configuration" + "\n"
        s += "+" + "+++++++++++++++++++++++++++++++++++++++++++++++\n"
        s += "+" + "Config File: " + self.config_file +"\n"
        s += "+" + "Fail Count: " +  str(self.fail_count) +"\n"
        s += "+" + "Eucalyptus Path: " +  str(self.eucapath) +"\n"
        s += "+" + "Credential Path: " +  str(self.credpath) +"\n"
        s += "+++++++++++++++++++++++++++++++++++++++++++++++++++++\n"
        return s

