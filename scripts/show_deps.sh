# [
#     {
#         "package": {
#             "key": "yarg",
#             "package_name": "yarg",
#             "installed_version": "0.1.9"
#         },
#         "dependencies": [
#             {
#                 "key": "requests",
#                 "package_name": "requests",
#                 "installed_version": "2.25.0",
#                 "required_version": null
#             }
#         ]
#     },
#     {
#         "package": {
#             "key": "zipp",
#             "package_name": "zipp",
#             "installed_version": "3.4.0"
#         },
#         "dependencies": []
#     }
# ]
echo "all_deps:"
pipdeptree -j

# absl==0.0
# absl_py==0.11.0
# apache_beam==2.44.0
# gin==0.1.006
# gin_config==0.5.0
# matplotlib==3.3.4
# mock==5.0.1
# nltk==3.8.1
# numpy==1.19.4
# opencv_python_headless==4.5.5.64
# pandas==1.1.5
# Pillow==9.4.0
# protobuf==4.21.12
# pycocotools==2.0.4
# PyYAML==6.0
# requests==2.25.0
# scikit_learn==1.2.0
# scipy==1.5.4
# sentencepiece==0.1.96
# seqeval==1.2.2
# setuptools==39.0.1
# six==1.15.0
# tensorflow==2.11.0
# tensorflow_addons==0.14.0
# tensorflow_datasets==4.5.2
# tensorflow_gpu==2.4.0
# tensorflow_hub==0.12.0
# tensorflow_model_optimization==0.7.2
# INFO: Successfully output requirements
echo "project_level_deps:"
pipreqs . --print