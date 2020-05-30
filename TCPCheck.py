#!/usr/bin/env python
# Copyright (c) 2020 Arista Networks, Inc.  All rights reserved.
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
   option VRF value mgmt
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
    - VRF is the VRF name to use for sending health checks. This is optional. If unset, then default VRF is used.


The CONF_FAIL and CONF_RECOVER files are just a list of commands to run at either Failure or at recovery. These commands
must be full commands just as if you were configuration the switch from the CLI.

For example the above referenced /mnt/flash/failed.conf file could include the following commands, which would
shutdown the BGP neighbor on failure:
router bgp 65001.65500
neighbor 10.1.1.1 shutdown

The recover.conf file would do the opposite and remove the shutdown statement:
router bgp 65001.65500
no neighbor 10.1.1.1 shutdown

This is of course just an example, and your use case would determine what config changes you'd make.

Please note, because this extension uses the EOS SDK eAPI interation module, you do not need to have 'enable and 'configure'
in your config change files. This is because, the EOS SDK eAPI interation module is already in configuration mode.
'''
#************************************************************************************
# Change log
# ----------
# Version 1.0.0  - 04/11/2018 - Jeremy Georges -- jgeorges@arista.com --  Initial Version
# Version 1.0.1  - 04/12/2018 - Jeremy Georges -- jgeorges@arista.com --  Format changes,HTTP REQ timeout added & URLPATH added
# Version 2.0.0  - 04/26/2018 - Jeremy Georges -- jgeorges@arista.com --  Added support for VRFs. Changed http code to use sockets
#                                                                         as this is currently the only supported method within SDK.
# Version 2.1.0  - 05/08/2018 - Jeremy Georges -- jgeorges@arista.com --  Changed eAPI interface to use the eAPI interaction module
#									  in EosSdk
# Version 2.1.1  - 05/30/2018 - Jeremy Georges -- jgeorges@arista.com --  Bug fix with non VRF socket call
# Version 2.2.0  - 05/14/2019 - Jeremy Georges -- jgeorges@arista.com -- Add a reason string to on_agent_enabled(self, enabled,reason=None)
#                                                                        to display why the agent is disabled (e.g. missing fail/recovery file)
# Version 2.3.1  - 05/30/2020 - Jeremy Georges -- jgeorges@arista.com -- Added additional exception handling and File Descriptor cleanup.
#                                                                        Changed Syslog to LOCAL4 so logs show up in EOS logs.
#*************************************************************************************
#
#
#****************************
# GLOBAL VARIABLES -        *
#****************************
__author__ = 'Jeremy Georges'
__version__ = '2.3.1'


#****************************
#*     MODULES              *
#****************************
#
import sys
import syslog
import eossdk
import re
import socket
import base64
import ssl
import os

#***************************
#*     CLASSES          *
#***************************
class TCPCheckAgent(eossdk.AgentHandler, eossdk.TimeoutHandler, eossdk.VrfHandler):
    def __init__(self, sdk, timeoutMgr, VrfMgr,EapiMgr):
        self.agentMgr = sdk.get_agent_mgr()
        self.tracer = eossdk.Tracer("TCPCheckPythonAgent")
        eossdk.AgentHandler.__init__(self, self.agentMgr)
        #Setup timeout handler
        eossdk.TimeoutHandler.__init__(self, timeoutMgr)
        self.tracer.trace0("Python agent constructed")
        eossdk.VrfHandler.__init__(self, VrfMgr)
        self.VrfMgr = VrfMgr
        self.EapiMgr= EapiMgr



        # These are the defaults. The config can override these
        # Default check Interval in seconds
        self.CHECKINTERVAL=5
        #
        # CURRENTSTATUS   1 is Good, 0 is Down
        self.CURRENTSTATUS=1

        # Default number of failures before determining a down neighbor
        self.FAILCOUNT=2
        #

        # Default HTTP Request timeout in seconds
        self.HTTPTIMEOUT=15

        # We need a global variable to use for failure counts. When we reach FAILCOUNT, then we'll
        # consider host/http down.
        self.FAILITERATION=0

        # CONFIGCHECK 1 if the config looks ok, 0 if bad. Will set this as semaphore for
        # basic configuration check
        self.CONFIGCHECK=1


        #Packetbuffer size for socket.recv()
        self.PACKETSIZE=20000


    def on_initialized(self):
        global __version__
        self.tracer.trace0("Initialized")
        syslog.syslog("TCPCheck Version %s Initialized" % __version__)
        self.agentMgr.status_set("Status:", "Administratively Up")
        # Lets check and set our state for each option during initialization.
        # i.e. after you do a 'no shut' on the daemon, we'll check each of these
        # and set status.

        # We'll pass this on to on_agent_option to process each of these.
        self.on_agent_option("IPv4", self.agentMgr.agent_option("IPv4"))
        self.on_agent_option("PROTOCOL", self.agentMgr.agent_option("PROTOCOL"))
        self.on_agent_option("TCPPORT", self.agentMgr.agent_option("TCPPORT"))
        self.on_agent_option("CONF_FAIL", self.agentMgr.agent_option("CONF_FAIL"))
        self.on_agent_option("CONF_RECOVER", self.agentMgr.agent_option("CONF_RECOVER"))
        self.on_agent_option("USERNAME", self.agentMgr.agent_option("USERNAME"))
        self.on_agent_option("PASSWORD", self.agentMgr.agent_option("PASSWORD"))
        self.on_agent_option("REGEX", self.agentMgr.agent_option("REGEX"))
        self.on_agent_option("URLPATH", self.agentMgr.agent_option("URLPATH"))
        self.on_agent_option("VRF", self.agentMgr.agent_option("VRF"))



        # Lets check the checkinterval, FAILCOUNT and HTTPTIMEOUT parameters and see if we should override the defaults
        # Note these are only variables that we have defaults for if user does not
        # override the value. Everything else, we'll reference the values directly
        # with agent.Mgr.agent_option("xyz")

        if self.agentMgr.agent_option("CHECKINTERVAL"):
            self.on_agent_option("CHECKINTERVAL", self.agentMgr.agent_option("CHECKINTERVAL"))
        else:
            # global CHECKINTERVAL
            # We'll just use the default time specified by global variable
            self.agentMgr.status_set("CHECKINTERVAL:", "%s" % self.CHECKINTERVAL)


        if self.agentMgr.agent_option("FAILCOUNT"):
            self.on_agent_option("FAILCOUNT", self.agentMgr.agent_option("FAILCOUNT"))
        else:
            # We'll just use the default failcount specified by global variable
            self.agentMgr.status_set("FAILCOUNT: ", "%s" % self.FAILCOUNT)


        # TODO - Perhaps add independent socket & HTTP timeout?

        if self.agentMgr.agent_option("HTTPTIMEOUT"):
            self.on_agent_option("HTTPTIMEOUT", self.agentMgr.agent_option("HTTPTIMEOUT"))
        else:
            # Since agent_option is not set, we'll just use the default HTTPTIMEOUT specified by global variable
            self.agentMgr.status_set("HTTPTIMEOUT:", "%s" % self.HTTPTIMEOUT)


        # Some basic mandatory variable checks. We'll check this when we have a
        # no shut on the daemon. Add some notes in comment and Readme.md to recommend
        # a shut and no shut every time you make parameter changes...
        if self.check_vars() == 1:
            self.CONFIGCHECK = 1
        else:
            self.CONFIGCHECK = 0
        #Start our handler now.
        self.agentMgr.status_set("HealthStatus:", "Unknown")
        self.timeout_time_is(eossdk.now())

    def on_timeout(self):
        '''
         This is the function/method where we do the exciting stuff :-)
        '''

        # If CONFIGCHECK is not 1 a.k.a. ok, then we won't do anything. It means we have a config error.
        if self.CONFIGCHECK == 1:
            # Let's check our HTTP Address & REGEX and see if its up or down.
            _web_check = self.web_check()
            if _web_check == 1:
                # Now we have to do all our health checking logic here...
                # If we are here, then we are up
                self.agentMgr.status_set("HealthStatus:", "UP")
                if self.CURRENTSTATUS == 0:
                    # We were down but now up,so now let's change the configuration and set CURRENTSTATUS to 1
                    # Run CONF_RECOVER ********
                    syslog.syslog("HTTP host back up. Changing Configuration.")
                    self.change_config('RECOVER')
                    self.CURRENTSTATUS = 1
                    self.FAILITERATION = 0
                elif self.FAILITERATION > 0:
                    # This means we had at least one miss but we did not change config, just log and reset variable to 0
                    syslog.syslog("HTTP host back up. Clearing FAILITERATION semaphore.")
                    self.agentMgr.status_set("HealthStatus:", "UP")
                    self.FAILITERATION = 0
            elif _web_check == 0:
                # We are down
                self.FAILITERATION += 1
                if self.CURRENTSTATUS == 0:
                    # This means we've already changed config. Do nothing.
                    pass
                else:
                    # These are strings, force them to ints
                    if self.agentMgr.agent_option("FAILCOUNT"):
                        MAXFAILCOUNT = self.agentMgr.agent_option("FAILCOUNT")
                    else:
                        # Else we'll use the default value of FAILCOUNT
                        MAXFAILCOUNT=self.FAILCOUNT
                    if int(self.FAILITERATION) >= int(MAXFAILCOUNT):
                        # Host is definitely down. Change config.
                        # RUN CONF_FAIL
                        syslog.syslog("HTTP HOST is down. Changing configuration.")
                        self.change_config('FAIL')
                        self.agentMgr.status_set("HealthStatus:", "FAIL")
                        self.CURRENTSTATUS = 0

            else:
                # We get here if we had some weird exception
                syslog.syslog("TCPCheck - An exception occurred. Skipping to next interval")

        # Wait for CHECKINTERVAL
        if self.agentMgr.agent_option("CHECKINTERVAL"):
            self.timeout_time_is(eossdk.now() + int(self.agentMgr.agent_option("CHECKINTERVAL")))
        else:
            self.timeout_time_is(eossdk.now() + int(self.CHECKINTERVAL))

    def on_agent_option(self, optionName, value):
        # options are a key/value pair
        # Here we set the status output when user does a show agent command
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
                self.agentMgr.status_set("HTTPTIMEOUT:", self.HTTPTIMEOUT)
            else:
                self.tracer.trace3("Adding HTTPTIMEOUT %s" % value)
                self.agentMgr.status_set("HTTPTIMEOUT:", "%s" % value)
        if optionName == "FAILCOUNT":
            if not value:
                self.tracer.trace3("FAILCOUNT Deleted")
                self.agentMgr.status_set("FAILCOUNT:", self.FAILCOUNT)
            else:
                self.tracer.trace3("Adding FAILCOUNT %s" % value)
                self.agentMgr.status_set("FAILCOUNT:", "%s" % value)
        if optionName == "CHECKINTERVAL":
            if not value:
                self.tracer.trace3("CHECKINTERVAL Deleted")
                self.agentMgr.status_set("CHECKINTERVAL:", self.CHECKINTERVAL)
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
        if optionName == "VRF":
            if not value:
                self.tracer.trace3("VRF Deleted")
                self.agentMgr.status_set("VRF:", "%s" % "Default")
            else:
                self.tracer.trace3("Adding VRF %s" % value)
                self.agentMgr.status_set("VRF:", "%s" % value)


    def on_agent_enabled(self, enabled,reason=None):
        # When shutdown set status and then shutdown
        if not enabled:
         self.tracer.trace0("Shutting down")
         self.agentMgr.status_del("Status:")
         if reason is not None:
             self.agentMgr.status_set("Status:", "Administratively Down - %s" % reason)
         else:
             self.agentMgr.status_set("Status:", "Administratively Down")
         self.agentMgr.agent_shutdown_complete_is(True)

    def check_vars(self):
        '''
        Do some basic config checking. Return 1 if all is good. Else return
        0 if config is missing a key parameter and send a syslog message so user
        knows what is wrong.
        Very basic existance testing here. Maybe add later some greater syntax testing...
        '''
        if not self.agentMgr.agent_option("TCPPORT"):
            syslog.syslog("TCPPORT Parameter is not set. This is a mandatory parameter")
            self.on_agent_enabled(enabled=False, reason='TCPPORT Parameter is not set')
            return 0
        if not self.agentMgr.agent_option("PROTOCOL"):
            syslog.syslog("PROTOCOL parameter is not set. This is a mandatory parameter")
            self.on_agent_enabled(enabled=False, reason='PROTOCOL Parameter is not set')
            return 0
        if self.agentMgr.agent_option("PROTOCOL") not in ('http', 'https'):
            syslog.syslog("PROTOCOL parameter is not valid. Parameter must be http or https")
            self.on_agent_enabled(enabled=False, reason='PROTOCOL parameter is not valid')
            return 0
        if not self.agentMgr.agent_option("IPv4"):
            syslog.syslog("IPv4 parameter is not set. This is a mandatory parameter")
            self.on_agent_enabled(enabled=False, reason='IPv4 parameter is not set')
            return 0
        if not self.agentMgr.agent_option("REGEX"):
            syslog.syslog("REGEX parameter is not set. This is a mandatory parameter")
            self.on_agent_enabled(enabled=False, reason='REGEX parameter is not set')
            return 0
        # Should add some basic file checking here...i.e. make sure the following files
        # actually exist.
        if not self.agentMgr.agent_option("CONF_FAIL"):
            syslog.syslog("CONF_FAIL parameter is not set. This is a mandatory parameter")
            self.on_agent_enabled(enabled=False, reason='CONF_FAIL parameter is not set')
            return 0
        if not self.agentMgr.agent_option("CONF_RECOVER"):
            syslog.syslog("CONF_RECOVER parameter is not set. This is a mandatory parameter")
            self.on_agent_enabled(enabled=False, reason='CONF_RECOVER parameter is not set')
            return 0

        # Check to see if files exist
        if self.agentMgr.agent_option("CONF_FAIL"):
            CONFFAILFILE = os.path.isfile(self.agentMgr.agent_option("CONF_FAIL"))
        if not CONFFAILFILE:
            syslog.syslog("CONF_FAIL file does NOT exist")
            self.on_agent_enabled(enabled=False, reason='CONF_FAIL file missing')
            return 0
        if self.agentMgr.agent_option("CONF_RECOVER"):
            CONFRECOVERFILE = os.path.isfile(self.agentMgr.agent_option("CONF_RECOVER"))
        if not CONFRECOVERFILE:
            syslog.syslog("CONF_RECOVER file does NOT exist")
            self.on_agent_enabled(enabled=False, reason='CONF_RECOVER file missing')
            return 0

        # If VRF option set, check to make sure it really exists.
        if self.agentMgr.agent_option("VRF"):
                if not self.VrfMgr.exists(self.agentMgr.agent_option("VRF")):
                    # This means the VRF does not exist
                    syslog.syslog("VRF %s does not exist." % self.agentMgr.agent_option("VRF"))
                    self.on_agent_enabled(enabled=False, reason='VRF does not exist')
                    return 0

        # If we get here, then we're good!
        return 1

    def web_check(self):
        '''
        This function will do HTTP/HTTPS Request and will return 1 if REGEX is found
        or 0 if not found or there are issues.
        '''

        # Let's build the correct URL
        if self.agentMgr.agent_option("URLPATH"):
            # We have a URLPATH we need to deal with.
            # Let's see if we have a leading / or not.
            if re.findall('^/', self.agentMgr.agent_option("URLPATH")):
                # This means we have a preceeding /
                FINALPATH="%s" % self.agentMgr.agent_option("URLPATH")
            else:
                FINALPATH="/%s" % self.agentMgr.agent_option("URLPATH")
        else:
            # If we get here, it means that URLPATH is not set, so lets just add a trailing / for consistency.
            FINALPATH="/"


        CRLF="\r\n"

        # Now lets build the request
        request = 'GET %s HTTP/1.1%s' % (FINALPATH, CRLF)
        request += 'HOST: %s%s' % (self.agentMgr.agent_option("IPv4"), CRLF)
        if self.agentMgr.agent_option("USERNAME"):
            token=base64.b64encode('%s:%s' % (self.agentMgr.agent_option("USERNAME"), self.agentMgr.agent_option("PASSWORD"))).decode("ascii")
            request += 'Authorization: Basic %s%s' % (token, CRLF)
        request += 'Connection: close%s' % CRLF


        if self.VrfMgr.exists(self.agentMgr.agent_option("VRF")):
            try:
                sock_fd=self.VrfMgr.socket_at(socket.AF_INET,socket.SOCK_STREAM,0,self.agentMgr.agent_option("VRF"))
                s = socket.fromfd( sock_fd, socket.AF_INET, socket.SOCK_STREAM, 0 )
                # Convert socket from type _socket.socket to socket._socketobject
                s = socket.socket ( _sock=s )
            except Exception as e:
                # If we get an issue, lets log this because we have an issue.
                s.close()
                syslog.syslog("Unable to create socket. Closing sock_fd")
                os.close(sock_fd)
                syslog.syslog("%s" % e)
                return 255
        else:
            try:
                s = socket.socket( socket.AF_INET, socket.SOCK_STREAM, 0 )
            except Exception as e:
                # If we get an issue, lets log this because we have an issue.
                syslog.syslog("Unable to create socket. Closing socket.")
                syslog.syslog("%s" % e)
                s.close()
                return 255

        if self.agentMgr.agent_option("PROTOCOL") == 'https':
            # Wrap in SSL
            try:
                thesocket = ssl.wrap_socket(s, ssl_version=ssl.PROTOCOL_TLSv1)
            except Exception as e:
                # If we get an issue, lets log this.
                s.close()
                if self.VrfMgr.exists(self.agentMgr.agent_option("VRF")):
                    # Because eossdk.VrfMgr.socket_at() provides a fileno, we'll just
                    # use os.close.
                    os.close(sock_fd)
                syslog.syslog("%s" % e)
                return 255
        else:
            # Whether we use s or thesocket, lets make thesocket be used moving forward for
            # http connection so I'm only using one object name here for crafting HTTP or HTTPS request...
            thesocket=s



        # Define server address and port
        serverAddress = ( self.agentMgr.agent_option("IPv4"), int(self.agentMgr.agent_option("TCPPORT")) )
        # Set timeout.
        if self.agentMgr.agent_option("HTTPTIMEOUT"):
            thesocket.settimeout(int(self.agentMgr.agent_option("HTTPTIMEOUT")))
        else:
            thesocket.settimeout(int(self.HTTPTIMEOUT))
        try:
            thesocket.connect( serverAddress )
        except:
            syslog.syslog("Connection Timeout")
            thesocket.close()
            if self.VrfMgr.exists(self.agentMgr.agent_option("VRF")):
                # Because eossdk.VrfMgr.socket_at() provides a fileno, we'll just
                # use os.close.
                os.close(sock_fd)
            # We get here if we can not establish connection to server. Return 0 same as
            # remote host down event.
            return 0

        thesocket.send(CRLF+request+CRLF+CRLF)
        pagecontent = thesocket.recv(self.PACKETSIZE)
        # pagecontent is a string, so I should be able to clean up sockets now.
        # and get that out of the way.

        # Cleanup
        thesocket.shutdown(socket.SHUT_RD)
        thesocket.close()
        if self.agentMgr.agent_option("PROTOCOL") == 'https':
            # Need to close the TCP socket too. Closing ssl socket doesn't always do this
            # If we get here, we had a legit SSL & TCP socket.
            s.shutdown(socket.SHUT_RD)
            s.close()
        if self.VrfMgr.exists(self.agentMgr.agent_option("VRF")):
            # Because eossdk.VrfMgr.socket_at() provides a fileno, we'll just
            # use os.close.
            os.close(sock_fd)




        # We could just return here because we got a page. But it would be more accurate
        # to do a Regex on the content so we know that things are legitimate.

        REGEX = self.agentMgr.agent_option("REGEX")

        # Now lets do regex match to make sure we got what was expected.
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
        If STATUS is FAIL, then run CONF_FAIL via eAPI API
        If STATUS RECOVER (or else) then run CONF_RECOVER via eAPI API
        '''
        CONF_FAIL = self.agentMgr.agent_option("CONF_FAIL")
        CONF_RECOVER = self.agentMgr.agent_option("CONF_RECOVER")
        if STATUS == 'FAIL':
            self.tracer.trace0("Status FAIL. Applying config changes")
            with open(CONF_FAIL) as fh:
                configfile = fh.readlines()
            # Strip out the whitespace
            configfile = [x.strip() for x in configfile]

            # Check to make sure user has not specified 'enable' as the first command. This will error  in command mode
            if configfile[0] == 'enable':
                del configfile[0]
            # Now apply config changes
            try:
                applyconfig = self.EapiMgr.run_config_cmds([z for z in configfile])
                if(applyconfig.success()):
                    syslog.syslog("Applied Configuration changes from %s" % CONF_FAIL)
                else:
                    syslog.syslog("Unable to apply configuration changes from %s" % CONF_FAIL)
            except:
                syslog.syslog("Unable to apply config via eAPI interaction module in EOS SDK.")
                return 0
        else:
            self.tracer.trace0("Status Recover. Applying config changes.")
            with open(CONF_RECOVER) as fh:
                configfile = fh.readlines()
            # Strip out the whitespace
            configfile = [x.strip() for x in configfile]

            # Check to make sure user has not specified 'enable' as the first command. This will error in command mode
            if configfile[0] == 'enable':
                del configfile[0]

            # Now apply config changes
            try:
                applyconfig = self.EapiMgr.run_config_cmds([z for z in configfile])
                if(applyconfig.success()):
                    syslog.syslog("Applied Configuration changes from %s" % CONF_RECOVER)
                else:
                    syslog.syslog("Unable to apply configuration changes from %s" % CONF_RECOVER)
            except:
                syslog.syslog("Unable to apply config via eAPI interaction module in EOS SDK.")
                return 0

        return 1


#=============================================
# MAIN
#=============================================
def main():
    syslog.openlog(ident="TCPCheck-ALERT-AGENT", logoption=syslog.LOG_PID, facility=syslog.LOG_LOCAL4)
    sdk = eossdk.Sdk()
    TCPCheck = TCPCheckAgent(sdk, sdk.get_timeout_mgr(),sdk.get_vrf_mgr(),sdk.get_eapi_mgr())
    sdk.main_loop(sys.argv)
    # Run the agent until terminated by a signal


if __name__ == "__main__":
    main()
