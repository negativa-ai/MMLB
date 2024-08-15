# MMLB
a framework for **M**easuring and analyzing **M**achine **L**earning deployment **B**loat.

NOTICE:
* More functionalities will be open-sourced.
* More detailed documentation will be added.
* We are working with Cimpilifier team to open source Cimplifier.

## Prerequisite
The code are tested on Ubuntu 18.04 with a Telsa T4 GPU.
Other platforms should also work.

## Set Up Environment
1. `conda create -n mmlb python=3.7 -y`
2. `conda activate mmlb`
3. `pip install -r requirements.txt`
4. `export CIMPLIFIER_SLIM_PATH=/path/to/your/climpfier/slim.py && export CIMPLIFIER_IMPORT_PATH=/path/to/your/cimplifier/import.py`
5. install Grype: https://github.com/anchore/grype

## Test Your Enviroment
1. `docker pull hfzhang6/tf_train_mnist && docker tag hfzhang6/tf_train_mnist tf_train_mnist`
2. `cd /path/to/project/src`
3. `python main.py --func=debloat --container_spec=../data/demo_imgs_spec.yml --output=../data/demo_debloat_results.csv`
If everything is set up correctly, a file named `generic_debloat_results.csv` will be created in the `../data` folder. 
The content is something like:
```
original_image_name,debloated_image_name,original_image_size,debloated_image_size,cmd
tf_train_mnist:latest,cimplifier_debloated_tf_train_mnist_latest_bin_python3,6506913911,1009632298,python3 /app/models/official/vision/image_classification/mnist_main.py --model_dir=./model_dir --data_dir=./data_dir --train_epochs=10 --distribution_strategy=one_device --num_gpus=1 --download
```
Check the Example section for a quick start.


## Example:
### Debloat a container
1. `export CIMPLIFIER_SLIM_PATH=/path/to/your/climpfier/slim.py && export CIMPLIFIER_IMPORT_PATH=/path/to/your/cimplifier/import.py`
1. `docker pull hfzhang6/tf_train_mnist && docker tag hfzhang6/tf_train_mnist tf_train_mnist`
2. `cd /path/to/project/src`
3. `python main.py --func=debloat --container_spec=../data/demo_imgs_spec.yml --output=./debloat_results.csv`

This will generate a file named `debloat_results.csv` and a debloated container named `cimplifier_debloated_tf_train_mnist_latest_bin_python3`.

### Container level analysis
1. `python main.py --func=diff  --i1=tf_train_mnist --i2=cimplifier_debloated_tf_train_mnist_latest_bin_python3 --i1_path=./debloated_files.csv --common_file_path=./common.csv --i2_path=./i2.csv`

The file `debloated_files.csv` lists the removed files. We will use this file to perform further analysis.

### Package Level Analysis 
1. `python main.py --func=pkg_analysis --container_spec=/home/ubuntu/repos/MMLB/data/demo_imgs_spec.yml`

Two files named `tf_train_mnist_packages.csv` and `tf_train_mnist_packages_files.csv` will be created in the current folder.


### Vulnerability Analysis
1. Generate the CVE report
```
python main.py --func=vul_analysis \
   --img_name=tf_train_mnist \
   --debloated_img_name=cimplifier_debloated_tf_train_mnist_latest_bin_python3 \
   --cmd=bash \
   --cve_number_path=./generic_container_cves.csv \
   --pkg_cve_number_path=./cves_by_pkg_in_original_container.csv
```

2. Move the grype report to current directory: `mv /tmp/grype.json .`



### Dependency Graph Analysis
1. Start a container run the following commands.
```
docker run -it --rm  -v /home/ubuntu/repos/MMLB/scripts/:/scripts  -v $PWD:/output tf_train_mnist:latest bash

# inside the container
pip install pipreqs
pip install pipdeptree
chmod +x /scripts/show_deps.sh
/scripts/show_deps.sh > /output/deps.txt
```

2. Generate depency graph
```
python main --func=pkg_deps_analysis \
   --img_name=tf_train_mnist \
   --debloated_img_name=cimplifier_debloated_tf_train_mnist_latest_bin_python3 \
   --removed_files_path=./debloated_files.csv \
   --package_path=./tf_train_mnist_packages.csv \
   --package_files_path=./tf_train_mnist_packages_files.csv \
   --deps_path=./deps.txt \
   --grype_json_path=./grype.json

```
This will generate two depenency graph figures in current forder, named `tf_train_mnist_pip.pdf` and `tf_train_mnist_apt.pdf`.
The former is the dependency graph of the pip packages and the latter is the dependency graph of the apt packages.
