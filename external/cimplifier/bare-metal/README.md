# cimplifier

This is simplified Cimplifier, only with slim functionality.

## Collect system logs

Start a container with your workload using: \
`docker run --cap-add=SYS_PTRACE -it <image_name> /bin/bash`\
This opens a shell in the container as well, run `ps` and remember the PID corresponding to bash.
In another terminal enter the container using: \
`docker exec -it [container-id] bash` \
At this point you should have two bash instances inside your container and the PID of the first terminal. \

Collecting logs can be done using _strace_. An example _strace_ call:
`strace -o container_strace -y -yy -f -ff -v -s 1024 -e trace=file -p 3847`
The most important parameters are:
* -o - defines the output files prefix
* -s - defines the maximum string size, if this is too small the parser will break as the "..." added for longer strings is not valid JSON.
* -p - defines the root PID to trace (PID of bash, that you got from _ps_)

Run the aforemention _strace_ command in the second bash terminal inside the container then run your workload in the traced bash shell, because the bash shell has strace attached then all processes that you run from the shell will be collected. \
After finishing your workload you can quit stracing with ctrl+c. The output of the strace will be files in the form of {-o parameter}.{pid}. In this version of cimplifier, partition.py expects only one singular strace file, which means the output strace files need to be concatenated (i.e by using `cat [-o parameter].* > logs.1` or if you used the _strace_ command shown before `cat container_strace.* > logs.1`). To get the log file out of docker use:
`docker cp [container-id]:[path to logfile] [path outside container]`, in my case it was be `docker cp charming_bohr:logs.1 .`, where charming_bohr was the automaticall generated docker container id.\

 It might be necessary to run your docker container with the flag "--cap-add=SYS_PTRACE" in order to give _strace_ the necessary permissions inside a docker container (more [info](https://jvns.ca/blog/2020/04/29/why-strace-doesnt-work-in-docker/)) 
 
### using modified `runc` binary for helping strace log automatically

The log capture could be automaticly when run the docker with a customized verison of `runc`.

Code could be found at [here](https://github.com/wy0917/runc/tree/v1.0.0-rc93-strace). Steps are mentioned in the commit message of [this commit](https://github.com/wy0917/runc/commit/4c17ec509d544fcdbcd4203d9bc45e081cb3d894).

After adding '-e TRACE=true' as the environment setting when executing `docker run`, then strace started automatically and log files could be found in folder /tmp/container-strace/_CONTAINER_ID_/, start pid is noted in file /tmp/container-strace/_CONTAINER_ID_/init.pid

## Run cimplifier

To use cimplifier you run partition.py, which takes 8 arguments:
* oldimg - Name of the target image
* newimgprefix - Name of the output image
* cntnr - Container made with the target image (should be the one you _strace_-d within)
* rootpid - The root PID you _strace_-d (-p parameter) 
* traces_file_name - path to strace file i.e /strace/logs.1
* volpath - mounted volume path, default `None`

An example of the full command is: \
`python3 bare-metal/code/slim.py  normal_nginx_ps_strace slim_normal_nginx_ps_strace baccff2b6110 1 logs.1`

After succesfully running partition.py, you will have generated a .tar and .json file. Running import.py with the path to the generated .json file will add the new image to docker. \
`python3 bare-metal/code/import.py slim_normal_nginx_ps_strace`
Note that .json is added inside the program.

## Example
Let's slim the nginx image!

1. Pull the nginx image: `docker pull nginx`
2. `strace` and `ps` are not included in the niginx image. We need to install these commands manually.
    1. run a nginx container: `docker run -it --name install_ps_strace nginx /bin/bash`
    2. install `strace` and `ps` in the container: `apt-get update && apt-get install procps && apt-get install strace`
    3. exit the container
    4. commit the container into a new image including `strace` and `ps`: `docker commit install_ps_strace nginx_ps_strace`
3. Run a conatiner using the image `nginx_ps_strace`; collect logs following the steps in the "Collect system logs" section. Note, you should run nginx inside the container with the cmd: `/docker-entrypoint.sh nginx -g 'daemon off;`. And curl the nginx server outside the container.
4. After collecting all the logs, run cimplifier following the steps in the "Run cimplifier" section
4. If everything works well, you should get the slimed image at this step. Run the slimed image using the cmd: `docker run -d -p 80:80 <name_of_slimmed_img> nginx -g 'daemon off;'`
5. Curl the server outside the container. It's expected to get a response saying "Welcome to nginx!" !


## Underhood
Here are the steps about how Cimplifier slims a container.
1. Collect system call logs using `strace` manually
2. Analyze the logs above to collect used files
3. Create a new image according to those used files from step 2.
    1. Get the configurations of the original image. The configs include Entrypoint, Cmd, Env, etc.
    2. Those configurations are saved into a JSON file called `json`. The file will be used to create a new image, i.e., debloated image.
    3. Export the original container to get all the files
    4. Copy the used files and zip them into a jar file called `layer.jar`.
    5. Combine the `json` and `laryer.jar` into an image tar file. This is the specification of an image tar file: https://github.com/moby/moby/blob/master/image/spec/v1.md
    6. `docker load` the above image tar file. Done!
