# TCPCheck 

The purpose of this utility is to test HTTP/HTTPS reachability, alert if its down and
run a config change. On recovery run another list of config changes.

## Author
Jeremy Georges - Arista Networks   - jgeorges@arista.com

## Description
TCPCheck Utility


The purpose of this utility is to test HTTP/HTTPS reachability, alert if its down and
run a config change. On recovery run another list of config changes.

Add the following configuration snippets to change the default behavior.  Current version supports only
one HOST and one port.


```
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
   option URLPATH value /index.html
   option PASSWORD value 4me2know
   option REGEX value HelloWorld 
   option VRF value mgmt
   no shutdown
```

```
Config Option explanation:
    - CHECKINTERVAL is the time in seconds to check the HTTP/S Neighbor(s). Default is 5 seconds.
    - FAILCOUNT is the number of times/iterations that the neighbor must fail before
    declaring the neighbor is down and executing config changes. This parameter is optional. The default is 2.
    - IPv4 is the address to check. Mandatory parameter.
    - HTTPTIMEOUT is the time in seconds we wait for an HTTP response. Default is 20 seconds.
    - PROTOCOL is either http,https. This is a mandatory parameter.
    - TCPPORT is the TCP port to use. Only applicable for HTTP/HTTPS. Mandatory parameter.
    - USERNAME is the username needed for HTTP request. If not set,
    then no username and password is sent.
    - PASSWORD is the password used for the HTTP request. If not set, then no password
    is used.
    - CONF_FAIL is the config file to apply the snippets of config changes. Mandatory parameter.
    - CONF_RECOVER is the config file to apply the snippets of config changes
    after recovery of Neighbor. Mandatory parameter.
    - REGEX is a regular expression to use to check the output of the http response. Mandatory parameter.
    - URLPATH is the specific path when forming the full URL. This is optional. Default is just the root '/'.
    - VRF is if you want the HTTP requests to use a specific VRF. This is optional. The default VRF will be used if not set.
```

The CONF_FAIL and CONF_RECOVER files are just a list of commands to run at failure or at recovery. These commands
should be **full commands** just as if you were configuration the switch from the CLI (i.e. not abbreviated commands).

For example the above referenced /mnt/flash/failed.conf file could include the following commands, which would
shutdown the BGP neighbor on failure:

```
router bgp 65001.65500
neighbor 10.1.1.1 shutdown
```

The recover.conf file would do the opposite and remove the shutdown statement:

```
router bgp 65001.65500
no neighbor 10.1.1.1 shutdown
```

This is of course just an example, and your use case would determine what config changes you'd make.

Please note, this uses the EOS SDK eAPI interaction module. You do not need to specify 'enable' and 'configure' in your 
configuration files, because it automatically goes into configuration mode.

This requires EOS SDK.
All new EOS releases include the SDK.

## Example

### Output of 'show daemon' command
```
Agent: TCPCheck (running with PID 14743)
Uptime: 0:11:18 (Start time: Sat May 30 17:19:58 2020)
Configuration:
Option              Value
------------------- -----------------------
CHECKINTERVAL       5
CONF_FAIL           /mnt/flash/failed.conf
CONF_RECOVER        /mnt/flash/recover.conf
FAILCOUNT           2
HTTPTIMEOUT         10
IPv4                192.168.100.103
PASSWORD            4me2know
PROTOCOL            https
REGEX               Arista
TCPPORT             443
URLPATH             /explorer.html
USERNAME            admin
VRF                 mgmt

Status:
Data                     Value
------------------------ -----------------------
CHECKINTERVAL:           5
CONF_FAIL:               /mnt/flash/failed.conf
CONF_RECOVER:            /mnt/flash/recover.conf
FAILCOUNT:               2
HTTPTIMEOUT:             10
HealthStatus:            UP
IPv4 Address List:       192.168.100.103
PASSWORD:                4me2know
PROTOCOL:                https
REGEX:                   Arista
Status:                  Administratively Up
TCPPORT:                 443
URLPATH:                 /explorer.html
USERNAME:                admin
VRF:                     mgmt
```

### Syslog Messages
```
May 30 16:48:45 DC1-SPINE-1 TCPCheck-ALERT-AGENT[12335]: %AGENT-6-INITIALIZED: Agent 'TCPCheck-TCPCheck' initialized; pid=12335
May 30 16:48:45 DC1-SPINE-1 TCPCheck-ALERT-AGENT[12335]: TCPCheck Version 2.3.1 Initialized
.
After HTTP Host goes down...
.
May 30 16:49:30 DC1-SPINE-1 TCPCheck-ALERT-AGENT[12335]: Connection Timeout
May 30 16:49:30 DC1-SPINE-1 TCPCheck-ALERT-AGENT[12335]: HTTP HOST is down. Changing configuration.
May 30 16:49:30 DC1-SPINE-1 TCPCheck-ALERT-AGENT[12335]: Applied Configuration changes from /mnt/flash/failed.conf
.
After Recover of HTTP Host...
.
May 30 16:49:57 DC1-SPINE-1 TCPCheck-ALERT-AGENT[12335]: HTTP host back up. Changing Configuration.
May 30 16:49:57 DC1-SPINE-1 ConfigAgent: %SYS-5-CONFIG_E: Enter configuration mode from console by root on UnknownTty (UnknownIpAddr)
May 30 16:49:57 DC1-SPINE-1 ConfigAgent: %SYS-5-CONFIG_I: Configured from console by root on UnknownTty (UnknownIpAddr)
May 30 16:49:57 DC1-SPINE-1 ConfigAgent: %SYS-5-CONFIG_E: Enter configuration mode from console by root on UnknownTty (UnknownIpAddr)
May 30 16:49:57 DC1-SPINE-1 ConfigAgent: %SYS-5-CONFIG_I: Configured from console by root on UnknownTty (UnknownIpAddr)
May 30 16:49:57 DC1-SPINE-1 TCPCheck-ALERT-AGENT[12335]: Applied Configuration changes from /mnt/flash/recover.conf
```



## INSTALLATION

Because newer releases of EOS require a SysdbMountProfile, you'll need two files - TCPCheck.py and TCPCheck.
TCPCheck.py will need to go to an appropriate location such as /mnt/flash and TCPCheck will need to be placed in 
/usr/lib/SysdbMountProfiles. The mount profile file name MUST match the python file name. In other words, if 
you place the mount profile TCPCheck in /usr/lib/SysdbMountProfiles as TCPCheck, then the executable filename TCPCheck.py 
must be changed to TCPCheck. The filename (agent name) and mount profile name must be the same.

An RPM has been included that allows you to easily just install TCPCheck as an extension and it takes care of all
the file requirements. The RPM also installs the TCPCheck SDK app in /usr/local/bin. This is the preferred distribution 
method for this extension.

This release has been tested on EOS 4.20.1, 4.20.4, 4.20.5 and 4.24.0. It is NOT currently supported on any releases beyond 4.29+ which transitioned
to Python3.

## Troubleshooting

### Agent crash due to Windows CR LF

If the agent is continuously crashing it might be caused by invalid characters in the python script. In the syslog outputs or `show logging` similar errors could be seen:

```
Aug 31 02:46:41 switch1 ProcMgr-worker: %PROCMGR-6-PROCESS_TERMINATED: 'TCPCheck' (PID=7319, status=32512) has terminated.
Aug 31 02:46:41 switch1 ProcMgr-worker: %PROCMGR-3-PROCESS_DELAYRESTART: 'TCPCheck' (PID=7319) restarted too often! Delaying restart for 120.0
```

To troubleshoot further the agent logs should be checked with `bash cat /var/log/agents/<agentName>-<pid>.log` (substitute `<agentName>-<pid>` with the actual name of the file

If the output is something similar as below, then it means the python script has an invalid `\r` which is the Windows carriage return (CR LF)

```
cat TCPCheck-Rack1-17880
==== Output from /mnt/flash/TCPCheck [] (PID=17880) started Sep 1 15:00:00.00000 ===
/usr/bin/env: 'python\r': No such file or directory
```

The solution is to convert the file to unix format, this can be done locally on EOS by editing the file with `vi` and typing `:set ff=unix`, so the steps would be:
- drop down to global configuration mode and shutdown the daemon
   ```configure
      daemon TCPCheck
        shutdown
   ```
- go to bash by typing `bash`
- `vi /mnt/flash/TCPCheck`
- type `:set ff=unix`
- press Enter
- press Esc
- type `:wq!`
- type `exit` to go back to EOS CLI and bring up the daemon again
- `no shutdown`

> Tip: When using Notepad++ to edit files always convert them to unix format by clicking on Edit - EOL Conversion and select Unix(LF) and save the file.

License
=======
BSD-3, See LICENSE file
