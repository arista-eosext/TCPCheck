#!/usr/bin/env python
# Copyright (c) 2018 Arista Networks, Inc.  All rights reserved.
# Arista Networks, Inc. Confidential and Proprietary.
'''
TCPCheck Utility

The purpose of this utility is to test HTTP/HTTPS reachability, alert if its down and
run a config change. On recovery run another list of config changes.

Add the following configuration snippets to change the default behavior.  Current version supports only
one HOST and one port.

daemon TCPCheck
   exec /usr/local/bin/TCPCheck
   option CHECKINTERVAL value 5
   option CONF_FAIL value /mnt/flash/failed.conf
   option CONF_RECOVER value /mnt/flash/recover.conf
   option FAILCOUNT value 2
   option HTTPTIMEOUT value 10
   option IPv4 value 10.1.1.1
   option PROTOCOL value https
   option TCPPORT value 443
   option USERNAME value admin
   option URLPATH value /explorer.html
   option PASSWORD value 4me2know
   option REGEX value eAPI
   no shutdown

This requires the EOS SDK extension installed if its < EOS 4.17.0 release.
All new EOS releases include the SDK.

Config Option explanation:
    - CHECKINTERVAL is the time in seconds to check the HTTP/S Neighbor(s). Default is 5 seconds.
    - FAILCOUNT is the number of times/iterations that the neighbor must fail before
declaring the neighbor is down and executing config changes. This is optional. The default is 2.
    - IPv4 is the address to check. Mandatory parameter.
    - HTTPTIMEOUT is the time in seconds we wait for an HTTP response. Default is 20 seconds.
    - PROTOCOL is either http,https. Mandatory parameter.
    - TCPPORT is the TCP port to use. Only applicable for HTTP/HTTPS. Mandatory parameter.
    - USERNAME is the username needed for HTTP request. If not set,
    then no username and password is sent.
    - PASSWORD is the password used for the HTTP request. If not set, then no password
    is used.
    - CONF_FAIL is the config file to apply the snippets of config changes. Mandatory parameter.
    - CONF_RECOVER is the config file to apply the snippets of config changes
    after recovery of Neighbor. Mandatory parameter.
    - REGEX is a regular expression to use to check the output of the http response. Mandatory parameter.
    - URLPATH is the specific path when forming the full URL. This is optional.


The CONF_FAIL and CONF_RECOVER files are just a list of commands to run at either Failure or at recovery. These commands
must be full commands just as if you were configuration the switch from the CLI.

For example the above referenced /mnt/flash/failed.conf file could include the following commands, which would
shutdown the BGP neighbor on failure:
enable
configure
router bgp 65001.65500
neighbor 10.1.1.1 shutdown

The recover.conf file would do the opposite and remove the shutdown statement:
enable
configure
router bgp 65001.65500
no neighbor 10.1.1.1 shutdown

This is of course just an example, and your use case would determine what config changes you'd make.
'''
#************************************************************************************
# Change log
# ----------
# Version 1.0.0  - 04/11/2018 - Jeremy Georges -- jgeorges@arista.com --  Initial Version
# Version 1.0.1  - 04/12/2018 - Jeremy Georges -- jgeorges@arista.com --  Format changes,HTTP REQ timeout added & URLPATH added
#
#*************************************************************************************
#
#
#****************************
#GLOBAL VARIABLES -         *
#****************************
# These are the defaults. The config can override these
#Default check Interval in seconds
CHECKINTERVAL=5
#
#CURRENTSTATUS   1 is Good, 0 is Down
CURRENTSTATUS=1

#Default number of failures before determining a down neighbor
FAILCOUNT=2
#

#Default HTTP Request timeout in seconds
HTTPTIMEOUT=20

# We need a global variable to use for failure counts. When we reach FAILCOUNT, then we'll
#consider host/http down.
FAILITERATION=0

#CONFIGCHECK 1 if the config looks ok, 0 if bad. Will set this as semaphore for
#basic configuration check
CONFIGCHECK=1

#****************************
#*     MODULES              *
#****************************
#
import sys
import syslog
import eossdk
import requests
import re
import jsonrpclib

#***************************
#*     CLASSES          *
#***************************
class TCPCheckAgent(eossdk.AgentHandler, eossdk.TimeoutHandler):
    def __init__(self, sdk, timeoutMgr):
        self.agentMgr = sdk.get_agent_mgr()
        self.tracer = eossdk.Tracer("TCPCheckPythonAgent")
        eossdk.AgentHandler.__init__(self, self.agentMgr)
        #Setup timeout handler
        eossdk.TimeoutHandler.__init__(self, timeoutMgr)
        self.tracer.trace0("Python agent constructed")


    def on_initialized(self):
        self.tracer.trace0("Initialized")
        syslog.syslog("TCPCheck Initialized")
        self.agentMgr.status_set("Status:", "Administratively Up")
        #Lets check and set our state for each option during initialization.
        #i.e. after you do a 'no shut' on the daemon, we'll check each of these
        #and set status.

        #We'll pass this on to on_agent_option to process each of these.
        self.on_agent_option("IPv4", self.agentMgr.agent_option("IPv4"))
        self.on_agent_option("PROTOCOL", self.agentMgr.agent_option("PROTOCOL"))
        self.on_agent_option("TCPPORT", self.agentMgr.agent_option("TCPPORT"))
        self.on_agent_option("CONF_FAIL", self.agentMgr.agent_option("CONF_FAIL"))
        self.on_agent_option("CONF_RECOVER", self.agentMgr.agent_option("CONF_RECOVER"))
        self.on_agent_option("USERNAME", self.agentMgr.agent_option("USERNAME"))
        self.on_agent_option("PASSWORD", self.agentMgr.agent_option("PASSWORD"))
        self.on_agent_option("REGEX", self.agentMgr.agent_option("REGEX"))
        self.on_agent_option("URLPATH", self.agentMgr.agent_option("URLPATH"))



        #Lets check the checkinterval, FAILCOUNT and HTTPTIMEOUT parameters and see if we should override the defaults
        #Note these are only variables that we have defaults for if user does not
        #override the value. Everything else, we'll reference the values directly
        #with agent.Mgr.agent_option("xyz")
        TESTINTERVAL = self.agentMgr.agent_option("CHECKINTERVAL")
        global CHECKINTERVAL
        if TESTINTERVAL:
            CHECKINTERVAL = TESTINTERVAL
            self.on_agent_option("CHECKINTERVAL", self.agentMgr.agent_option("CHECKINTERVAL"))
        else:
            #global CHECKINTERVAL
            #We'll just use the default time specified by global variable
            self.agentMgr.status_set("CHECKINTERVAL:", "%s" % CHECKINTERVAL)

        TESTFAILCOUNT = self.agentMgr.agent_option("FAILCOUNT")
        global FAILCOUNT
        if TESTFAILCOUNT:
            FAILCOUNT = TESTFAILCOUNT
            self.on_agent_option("FAILCOUNT", self.agentMgr.agent_option("FAILCOUNT"))
        else:
            #We'll just use the default failcount specified by global variable
            self.agentMgr.status_set("FAILCOUNT: ", "%s" % FAILCOUNT)

        TESTHTTPTIMEOUT = self.agentMgr.agent_option("HTTPTIMEOUT")
        global HTTPTIMEOUT
        if TESTHTTPTIMEOUT:
            HTTPTIMEOUT = TESTHTTPTIMEOUT
            self.on_agent_option("HTTPTIMEOUT", self.agentMgr.agent_option("HTTPTIMEOUT"))
        else:
            #Since agent_option is not set, we'll just use the default HTTPTIMEOUT specified by global variable
            self.agentMgr.status_set("HTTPTIMEOUT:", "%s" % HTTPTIMEOUT)


        #Some basic mandatory variable checks. We'll check this when we have a
        #no shut on the daemon. Add some notes in comment and Readme.md to recommend
        #a shut and no shut every time you make parameter changes...
        global CONFIGCHECK
        if self.check_vars() == 1:
            CONFIGCHECK = 1
        else:
            CONFIGCHECK = 0
        #Start our handler now.
        self.agentMgr.status_set("HealthStatus:", "Unknown")
        self.timeout_time_is(eossdk.now())

    def on_timeout(self):
        '''
         This is the function/method where we do the exciting stuff :-)
        '''
        #Global variables are needed
        global CHECKINTERVAL
        global FAILCOUNT
        global CONFIGCHECK
        global FAILITERATION
        global CURRENTSTATUS
        global CONFIGCHECK
        global HTTPTIMEOUT

        #We need to set the status variables here, in case user makes changes to config after initial config.
        #self.agentMgr.status_set("HTTPTIMEOUT:", HTTPTIMEOUT)


        if CONFIGCHECK == 1:
            #Let's check our HTTP Address & REGEX and see if its up or down.
            if self.web_check() == 1:
                #Now we have to do all our health checking logic here...
                #If we are here, then we are up
                self.agentMgr.status_set("HealthStatus:", "UP")
                if CURRENTSTATUS == 0:
                    #We were down but now up,so now let's change the configuration and set CURRENTSTATUS to 1
                    #Run CONF_RECOVER ********
                    self.change_config('RECOVER')
                    syslog.syslog("HTTP host back up. Changing Configuration.")
                    CURRENTSTATUS = 1
                    FAILITERATION = 0
                elif FAILITERATION > 0:
                    #This means we had at least one miss but we did not change config, just log and reset variable to 0
                    syslog.syslog("HTTP host back up. Clearing FAILITERATION semaphore.")
                    self.agentMgr.status_set("HealthStatus:", "UP")
                    FAILITERATION = 0
            else:
                #We are down
                FAILITERATION += 1
                if CURRENTSTATUS == 0:
                    #This means we've already changed config. Do nothing.
                    pass
                else:
                    # These are strings, force them to ints
                    if int(FAILITERATION) >= int(FAILCOUNT):
                        #Host is definitely down. Change config.
                        #RUN CONF_FAIL
                        self.change_config('FAIL')
                        self.agentMgr.status_set("HealthStatus:", "FAIL")
                        CURRENTSTATUS = 0
                        syslog.syslog("HTTP HOST is down. Changing configuration.")



        #Wait for CHECKINTERVAL
        self.timeout_time_is(eossdk.now() + int(CHECKINTERVAL))

    def on_agent_option(self, optionName, value):
        #options are a key/value pair
        #Here we set the status output when user does a show agent command
        if optionName == "IPv4":
            if not value:
                self.tracer.trace3("IPv4 List Deleted")
                self.agentMgr.status_set("IPv4 Address List:", "None")
            else:
                self.tracer.trace3("Adding IPv4 Address list to %s" % value)
                self.agentMgr.status_set("IPv4 Address List:", "%s" % value)
        if optionName == "PROTOCOL":
            if not value:
                self.tracer.trace3("Protocol Deleted")
                self.agentMgr.status_set("PROTOCOL:", "None")
            else:
                self.tracer.trace3("Adding Protocol %s" % value)
                self.agentMgr.status_set("PROTOCOL:", "%s" % value)
        if optionName == "TCPPORT":
            if not value:
                self.tracer.trace3("TCPPORT Deleted")
                self.agentMgr.status_set("TCPPORT:", "None")
            else:
                self.tracer.trace3("Adding TCPPORT %s" % value)
                self.agentMgr.status_set("TCPPORT:", "%s" % value)
        if optionName == "USERNAME":
            if not value:
                self.tracer.trace3("USERNAME Deleted")
                self.agentMgr.status_set("USERNAME:", "None")
            else:
                self.tracer.trace3("Adding USERNAME %s" % value)
                self.agentMgr.status_set("USERNAME:", "%s" % value)
        if optionName == "PASSWORD":
            if not value:
                self.tracer.trace3("PASSWORD Deleted")
                self.agentMgr.status_set("PASSWORD:", "None")
            else:
                self.tracer.trace3("Adding PASSWORD %s" % value)
                self.agentMgr.status_set("PASSWORD:", "%s" % value)
        if optionName == "CONF_FAIL":
            if not value:
                self.tracer.trace3("CONF_FAIL Deleted")
                self.agentMgr.status_set("CONF_FAIL:", "None")
            else:
                self.tracer.trace3("Adding CONF_FAIL %s" % value)
                self.agentMgr.status_set("CONF_FAIL:", "%s" % value)
        if optionName == "CONF_RECOVER":
            if not value:
                self.tracer.trace3("CONF_RECOVER Deleted")
                self.agentMgr.status_set("CONF_RECOVER:", "None")
            else:
                self.tracer.trace3("Adding CONF_RECOVER %s" % value)
                self.agentMgr.status_set("CONF_RECOVER:", "%s" % value)
        if optionName == "REGEX":
            if not value:
                self.tracer.trace3("REGEX Deleted")
                self.agentMgr.status_set("REGEX:", "None")
            else:
                self.tracer.trace3("Adding REGEX %s" % value)
                self.agentMgr.status_set("REGEX:", "%s" % value)
        if optionName == "HTTPTIMEOUT":
            if not value:
                self.tracer.trace3("HTTPTIMEOUT Deleted")
                self.agentMgr.status_set("HTTPTIMEOUT:", HTTPTIMEOUT)
            else:
                self.tracer.trace3("Adding HTTPTIMEOUT %s" % value)
                self.agentMgr.status_set("HTTPTIMEOUT:", "%s" % value)
        if optionName == "FAILCOUNT":
            if not value:
                self.tracer.trace3("FAILCOUNT Deleted")
                self.agentMgr.status_set("FAILCOUNT:", FAILCOUNT)
            else:
                self.tracer.trace3("Adding FAILCOUNT %s" % value)
                self.agentMgr.status_set("FAILCOUNT:", "%s" % value)
        if optionName == "CHECKINTERVAL":
            if not value:
                self.tracer.trace3("CHECKINTERVAL Deleted")
                self.agentMgr.status_set("CHECKINTERVAL:", CHECKINTERVAL)
            else:
                self.tracer.trace3("Adding CHECKINTERVAL %s" % value)
                self.agentMgr.status_set("CHECKINTERVAL:", "%s" % value)
        if optionName == "URLPATH":
            if not value:
                self.tracer.trace3("URLPATH Deleted")
                self.agentMgr.status_set("URLPATH:", "%s" % "/")
            else:
                self.tracer.trace3("Adding URLPATH %s" % value)
                self.agentMgr.status_set("URLPATH:", "%s" % value)



    def on_agent_enabled(self, enabled):
        #When shutdown set status and then shutdown
        if not enabled:
            self.tracer.trace0("Shutting down")
            self.agentMgr.status_del("Status:")
            self.agentMgr.status_set("Status:", "Administratively Down")
            self.agentMgr.agent_shutdown_complete_is(True)

    def check_vars(self):
        '''
        Do some basic config checking. Return 1 if all is good. Else return
        0 if config is missing a key parameter and send a syslog message so user
        knows what is wrong.
        Very basic existance testing here. Maybe add later some syntax testing...
        '''
        if not self.agentMgr.agent_option("TCPPORT"):
            syslog.syslog("TCPPORT Parameter is not set. This is a mandatory parameter")
            return 0
        if not self.agentMgr.agent_option("PROTOCOL"):
            syslog.syslog("PROTOCOL parameter is not set. This is a mandatory parameter")
            return 0
        if self.agentMgr.agent_option("PROTOCOL") not in ('http', 'https'):
            syslog.syslog("PROTOCOL parameter is not valid. Parameter must be http or https")
            return 0
        if not self.agentMgr.agent_option("IPv4"):
            syslog.syslog("IPv4 parameter is not set. This is a mandatory parameter")
            return 0
        if not self.agentMgr.agent_option("REGEX"):
            syslog.syslog("REGEX parameter is not set. This is a mandatory parameter")
            return 0
        #Should add some basic file checking here...i.e. make sure the following files
        #actually exist.
        if not self.agentMgr.agent_option("CONF_FAIL"):
            syslog.syslog("CONF_FAIL parameter is not set. This is a mandatory parameter")
            return 0
        if not self.agentMgr.agent_option("CONF_RECOVER"):
            syslog.syslog("CONF_RECOVER parameter is not set. This is a mandatory parameter")
            return 0
        #TO DO
        #Add checks for CONF_FAIL and CONF_RECOVER files. Make sure they at least exist on FS.

        #If we get here, then we're good!
        return 1

    def web_check(self):
        '''
        This function will do HTTP/HTTPS Request and will return 1 if REGEX is found
        or 0 if not found or there are issues.
        '''
        IPv4 = self.agentMgr.agent_option("IPv4")
        PROTO = self.agentMgr.agent_option("PROTOCOL")
        TCPPORT = self.agentMgr.agent_option("TCPPORT")
        USERNAME = self.agentMgr.agent_option("USERNAME")
        PASSWORD = self.agentMgr.agent_option("PASSWORD")
        REGEX = self.agentMgr.agent_option("REGEX")
        global HTTPTIMEOUT

        #Let's build the correct URL

        if self.agentMgr.agent_option("URLPATH"):
            #We have a URLPATH we need to deal with.
            #Let's see if we have a leading / or not.
            if re.findall('^/', self.agentMgr.agent_option("URLPATH")):
                # This means we have a preceeding /
                FINALPATH="%s" % self.agentMgr.agent_option("URLPATH")
            else:
                FINALPATH="/%s" % self.agentMgr.agent_option("URLPATH")
        else:
            #If we get here, it means that URLPATH is not set, so lets just add a trailing / for consistency.
            FINALPATH="/"


        if self.agentMgr.agent_option("PROTOCOL") == 'https':
            URL = 'https://%s:%s%s' % (IPv4, TCPPORT,FINALPATH)

        else:
            URL = 'http://%s:%s%s' % (IPv4, TCPPORT,FINALPATH)
        if not USERNAME:
            try:
                pagecontent = requests.get(URL, verify=False,timeout=int(HTTPTIMEOUT)).content
            except requests.exceptions.RequestException as e:
                self.tracer.trace0("Error when requesting web page %s" % e)
                return 0
        else:
            try:
                pagecontent = requests.get(URL, auth=(USERNAME, PASSWORD), verify=False,timeout=int(HTTPTIMEOUT)).content
            except requests.exceptions.RequestException as e:
                self.tracer.trace0("Error when requesting web page %s" % e)
                return 0
        # We could just return here because we got a page. But it would be more accurate
        #to do a Regex on the content so we know that things are legitimate.


        #Now lets do regex match to make sure we got what was expected.
        if pagecontent:
            if re.findall(REGEX, pagecontent):
                self.tracer.trace0("REGEX %s found" % REGEX)
                return 1
            else:
                self.tracer.trace0("REGEX %s NOT found" % REGEX)
                return 0
        else:
            self.tracer.trace0("WEB Content is blank")
            return 0

    def change_config(self, STATUS):
        '''
        Method to change configuration of switch.
        If STATUS is FAIL, then run CONF_FAIL via eAPI
        If STATUS RECOVER (or else) then run CONF_RECOVER via eAPI
        '''
        CONF_FAIL = self.agentMgr.agent_option("CONF_FAIL")
        CONF_RECOVER = self.agentMgr.agent_option("CONF_RECOVER")
        if STATUS == 'FAIL':
            self.tracer.trace0("Status FAIL. Applying config changes")
            with open(CONF_FAIL) as fh:
                configfile = fh.readlines()
            #Strip out the whitespace
            configfile = [x.strip() for x in configfile]

            #Now apply config changes
            switch = jsonrpclib.Server("unix:/var/run/command-api.sock")
            try:
                switch.runCmds(1, [x for x in configfile], "json")
            except:
                syslog.syslog("Unable to apply config via eAPI. Is Unix protocol enabled?")
                return 0
        else:
            self.tracer.trace0("Status Recover. Applying config changes.")
            with open(CONF_RECOVER) as fh:
                configfile = fh.readlines()
            #Strip out the whitespace
            configfile = [x.strip() for x in configfile]

            #Now apply config changes
            switch = jsonrpclib.Server("unix:/var/run/command-api.sock")
            try:
                switch.runCmds(1, [x for x in configfile], "json")
            except:
                syslog.syslog("Unable to apply config via eAPI. Is Unix protocol enabled?")
                return 0

        return 1


#=============================================
# MAIN
#=============================================
def main():
    syslog.openlog(ident="TCPCheck-ALERT-AGENT", logoption=syslog.LOG_PID, facility=syslog.LOG_LOCAL0)
    sdk = eossdk.Sdk()
    TCPCheck = TCPCheckAgent(sdk, sdk.get_timeout_mgr())
    sdk.main_loop(sys.argv)
    # Run the agent until terminated by a signal


if __name__ == "__main__":
    main()
