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

##  Debloat Containers

## Container Level Analysis
1. debloat a container:
```
python main.py --func=debloat --container_spec=/path/to/your/input_imgs_spec.yml --output=./generic_debloat_res.csv
```
2. Diff the file systems of the original container and the debloated version:
```
python main.py --func=diff  --i1=ORIGINAL_IMAGE_NAME --i2=DEBLOATED_IMAGE_NAME --i1_path=./i1.csv --common_file_path=./common.csv --i2_path=./i2.csv
```

## Package Level Analysis and Dependency Graph Analysis
To Be Done

## Vulnerability Analysis
```
python main.py --func=vul_analysis \
   --img_meta_path=/path/to/generic_image_meta.json \
   --cve_number_path=/path/to/generic_container_cves.csv \
   --pkg_cve_number_path=/path/to/cves_by_pkg_in_original_container.csv
```
