# cimplifier
Attention: This is a fork of the original cimplifier repository. The container partition feature is removed and only the slim feature is retained.
## Overview of cimplifier
Given a container and simple user-defined
constraints, the tool partitions it into simpler containers, which
(i) are isolated from each other, only communicating as necessary,
and (ii) only include enough resources to perform their functionality. Evaluation on real-world containers demonstrates that
Cimplifier preserves the original functionality, leads to reduction
in image size of up to 95%. The tool partitions at the level of application executables. System call logs from executions of a given container are collected which are used to construct resource sets for different component
executables. Based on flexible, pluggable policies container partitions are determined. The resulting containers are populated with
the resources needed for correct functioning of the executables.
Container mechanisms themselves provide for separation of resources. Based on the resource sets identified, this separation is relaxed to share some resources across containers on an as-needed basis. 

The tool supports three partitioning types:
+ All-one-context: There is no partitioning, this is tantamount to container-slimming
+ One-one-context: Each executable is placed in its own container, meant for testing the tool
+ Disjoint-subsets-context: The user specifies disjoint subsets of executables, each subset corresponding to a different container. Executables not specified can be placed in any container.

To run cimplifier you need to collect system call logs. 
This can be done using `strace`. An example `strace` call:
```shell
strace -o PREFIX -y -yy -f -ff -v -s 1024 -e trace=file -p 506
```

The most important parameters are:
* -o - defines the output files prefix
* -s - defines the maximum string size, if this is too small the parser will break as the "..." added for longer strings is not valid JSON.
* -p - defines the root PID to trace

## How to strace
### Stracing inside target container
One way to strace is to have two bash shells in the target container. Getting the PID for the bash inside the container using _ps_ and then running _strace_ on that PID in the other shell, then running your workload in the traced shell. The output of the strace will be files in the form of {-o parameter}.{pid}. In this version of cimplifier, partition.py expects only one singular strace file, which means the output strace files need to be concatenated (i.e by using _cat_). It might be necessary to run your docker container with the flag "--cap-add=SYS_PTRACE" in order to give _strace_ the necessary permissions inside a docker container (more [info](https://jvns.ca/blog/2020/04/29/why-strace-doesnt-work-in-docker/))

### Stracing inside vagrant VM
Using command `ps -aef --forest` to list the processes currently running, the following output could be found:
```
root       505     1  0 13:31 ?        00:00:00 /bin/sh -c /usr/bin/docker daemon            --exec-opt native.cgroupdriver=systemd          
root       506   505  0 13:31 ?        00:00:04  \_ /usr/bin/docker daemon --exec-opt native.cgroupdriver=systemd --selinux-enabled --log-dri
root      4138   506  0 13:42 ?        00:00:00  |   \_ docker-proxy -proto tcp -host-ip 0.0.0.0 -host-port 80 -container-ip 172.17.0.2 -cont
root       507   505  0 13:31 ?        00:00:00  \_ /usr/bin/forward-journald -tag docker
```
So, `506` is the docker daemon process id, which is used in the strace command.
After strace attach to the process, fireup the docker instance, `nginx` for example.
```shell
docker run --pid host -p 80:80 -d nginx
```
After the container is running the ps command will have new child process attached:
```
root       505     1  0 13:31 ?        00:00:00 /bin/sh -c /usr/bin/docker daemon            --exec-opt native.cgroupdriver=systemd          
root       506   505  0 13:31 ?        00:00:04  \_ /usr/bin/docker daemon --exec-opt native.cgroupdriver=systemd --selinux-enabled --log-dri
root      4138   506  0 13:42 ?        00:00:00  |   \_ docker-proxy -proto tcp -host-ip 0.0.0.0 -host-port 80 -container-ip 172.17.0.2 -cont
root      4144   506  0 13:42 ?        00:00:00  |   \_ nginx: master process nginx -g daemon off;
101       4176  4144  0 13:42 ?        00:00:00  |       \_ nginx: worker process
root       507   505  0 13:31 ?        00:00:00  \_ /usr/bin/forward-journald -tag docker
```
From this log `4144` is the pid of the container process which have `pivot_root` call inside the log，so this will be the `rootpid` for partition.py.

Note: this is only works in the vagrant environment, and not working anymore with newer docker engine, since the change from `docker daemon` to `containerd`.

## Running Cimplifier

Then to use cimplifier you run partition.py, which takes 8 arguments:
* oldimg - Name of the target image
* newimgprefix - Name of the output image
* cntnr - Container made with the target image (should be the one you _strace_-d)
* rootpid - The root PID you _strace_-d (-p parameter)
* traces_prefix - path to strace files + prefix i.e /strace/container_strace
* stubexe - path to cexec/client
* executorexe - path to cexec/server
* volpath - path to output

After succesfully running partition.py, you will have generated a .tar and .json file. Running import.py with the path to the generated .json file will add the new image to docker.


## Show Case (Nginx Container)
[Debloating Nginx Container here](./env_vagrant/debloating_nginx.md)
