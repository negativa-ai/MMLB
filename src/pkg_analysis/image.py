import json
import os

import docker
import matplotlib.pyplot as plt

from pkg_analysis.package import PkgFile
from vul_analysis.vul_analysis import ContainerCreator

CRITICAL = "Critical"
HIGH = "High"
MEDIUM = "Medium"
LOW = "Low"
NEGLIBILE = "Negligible"


class Image(object):
    def __init__(self, image, cache_path="./img_summary.json") -> None:
        self.image = image
        self.cache_path = cache_path

    def _plot(self):
        labels = ["apt_size(KB)", "pip_size(KB)", "conda_size(KB)", "others_size(KB)"]
        total_size = self.original_image_desc["image_size(KB)"]
        values = []
        for k in labels:
            values.append(self.original_image_desc[k] / total_size)

        fig1, axs = plt.subplots(2)
        axs[0].pie(values, labels=labels, autopct="%1.1f%%", shadow=True, startangle=90)
        axs[0].axis("equal")
        axs[0].set_title("origin")

        debloated_labels = [
            "debloated_apt_size(KB)",
            "debloated_pip_size(KB)",
            "debloated_conda_size(KB)",
            "debloated_others_size(KB)",
        ]
        debloated_total_size = self.debloated_desc["total_debloated_size(KB)"]
        debloated_values = []
        for k in debloated_labels:
            debloated_values.append(self.debloated_desc[k] / debloated_total_size)

        axs[1].pie(
            debloated_values,
            labels=debloated_labels,
            autopct="%1.1f%%",
            shadow=True,
            startangle=90,
        )
        axs[1].axis("equal")
        axs[1].set_title("debloated")
        plt.show()

    def _cache_img_summary(self):
        if not os.path.exists(self.cache_path):
            with open(self.cache_path, "w") as f:
                f.write("{}")

        with open(self.cache_path, "r") as f:
            data = json.load(f)
            data[self.image] = self.size
            data[self.debloated_img_name] = self.debloated_img_size

        with open(self.cache_path, "w") as f:
            json.dump(data, f)

    # todo: another subclass of image, targeted for debloated imgs
    def analyze(
        self,
        debloated_img_name,
        package_files_df,
        debloated_files_df,
        pkg_df,
        plot=True,
        use_cache=False,
    ):
        self.debloated_img_name = debloated_img_name
        if not use_cache:
            self.api_client = docker.APIClient(base_url="unix://var/run/docker.sock")
            info = self.api_client.inspect_image(self.image)
            self.size = round(info["Size"] * 1.0 / 1024, 2)  # (kb)

            info = self.api_client.inspect_image(debloated_img_name)
            self.debloated_img_size = round(info["Size"] * 1.0 / 1024, 2)  # (kb)
        else:
            print("use cache img summary.")
            with open(self.cache_path, "r") as f:
                data = json.load(f)
                self.size = data[self.image]
                self.debloated_img_size = data[self.debloated_img_name]
        self._cache_img_summary()

        original_image_desc = {"image_size(KB)": self.size}

        self.pkg_df = pkg_df

        # these file paths are dispaly file paths, it can be like this:
        # '/usr/local/lib/python3.8/dist-packages/../../../bin/tqdm'
        self.package_files_df = package_files_df.sort_values(
            by=["size(KB)"], ascending=False
        )
        # convert to normpath
        self.package_files_df["path"] = self.package_files_df["path"].apply(
            os.path.normpath
        )

        pkg_by_type = self.package_files_df.groupby("package_type").sum()
        self.pacakge_sizes_df = (
            self.package_files_df.groupby(["package", "package_type", "version"])
            .sum()
            .sort_values("size(KB)", ascending=False)
        )
        original_image_desc["apt_size(KB)"] = pkg_by_type.loc["apt"]["size(KB)"]
        original_image_desc["pip_size(KB)"] = 0
        if "pip" in pkg_by_type.index:
            original_image_desc["pip_size(KB)"] = pkg_by_type.loc["pip"]["size(KB)"]

        original_image_desc["conda_size(KB)"] = 0
        if "conda" in pkg_by_type.index:
            original_image_desc["conda_size(KB)"] = pkg_by_type.loc["conda"]["size(KB)"]

        original_image_desc["total_package_size(KB)"] = self.package_files_df[
            "size(KB)"
        ].sum()
        original_image_desc["others_size(KB)"] = (
            original_image_desc["image_size(KB)"]
            - original_image_desc["total_package_size(KB)"]
        )

        assert (
            original_image_desc["apt_size(KB)"]
            + original_image_desc["conda_size(KB)"]
            + original_image_desc["pip_size(KB)"]
            == original_image_desc["total_package_size(KB)"]
        )

        self.deboated_files_df = debloated_files_df.sort_values(
            by=["size(KB)"], ascending=False
        )

        debloated_desc = {
            "total_debloated_size(KB)": self.deboated_files_df["size(KB)"].sum()
        }
        debloated_desc["debloated_img_size(KB)"] = self.debloated_img_size

        self.debloated_pkg_files_df = (
            self.deboated_files_df.set_index("name")
            .join(self.package_files_df.set_index("path"), lsuffix="debloated")
            .dropna()[["size(KB)", "package", "version", "package_type"]]
            .sort_values(by=["size(KB)"], ascending=False)
        )

        debloated_pkg_by_type = self.debloated_pkg_files_df.groupby(
            "package_type"
        ).sum()
        debloated_desc["debloated_apt_size(KB)"] = 0
        if "apt" in debloated_pkg_by_type.index:
            debloated_desc["debloated_apt_size(KB)"] = debloated_pkg_by_type.loc["apt"][
                "size(KB)"
            ]
        debloated_desc["debloated_pip_size(KB)"] = 0
        if "pip" in debloated_pkg_by_type.index:
            debloated_desc["debloated_pip_size(KB)"] = debloated_pkg_by_type.loc["pip"][
                "size(KB)"
            ]
        debloated_desc["debloated_conda_size(KB)"] = 0
        if "conda" in debloated_pkg_by_type.index:
            debloated_desc["debloated_conda_size(KB)"] = debloated_pkg_by_type.loc[
                "conda"
            ]["size(KB)"]
        debloated_desc["total_debloated_pkg_size(KB)"] = self.debloated_pkg_files_df[
            "size(KB)"
        ].sum()

        assert (
            debloated_desc["debloated_apt_size(KB)"]
            + debloated_desc["debloated_conda_size(KB)"]
            + debloated_desc["debloated_pip_size(KB)"]
            == debloated_desc["total_debloated_pkg_size(KB)"]
        )
        debloated_desc["debloated_others_size(KB)"] = (
            debloated_desc["total_debloated_size(KB)"]
            - debloated_desc["total_debloated_pkg_size(KB)"]
        )

        self.debloated_other_files_df = debloated_files_df.set_index("name").drop(
            self.debloated_pkg_files_df.index
        )

        self.pkg_sizes = (
            self.package_files_df.groupby(["package", "package_type", "version"])
            .sum()
            .sort_values(by=["size(KB)"], ascending=False)
        )

        self.debloated_pkg_sizes = (
            self.debloated_pkg_files_df.groupby(["package", "package_type", "version"])
            .sum()
            .sort_values(by=["size(KB)"], ascending=False)
        )
        # we should keep all packages detected, so use how='right'
        self.pkg_bloat_degrees = self.debloated_pkg_sizes.join(
            self.pkg_sizes, rsuffix="_total", lsuffix="_debloated", how="right"
        ).fillna(0)
        self.pkg_bloat_degrees["bloat_degree"] = (
            self.pkg_bloat_degrees["size(KB)_debloated"]
            / self.pkg_bloat_degrees["size(KB)_total"]
        )
        self.pkg_bloat_degrees = self.pkg_bloat_degrees.join(
            self.pkg_df.set_index(["package", "package_type", "version"])
        ).drop("container", axis=1)

        self.original_image_desc = original_image_desc
        self.debloated_desc = debloated_desc

        if plot:
            self._plot()
        return original_image_desc, debloated_desc

    def set_pkg_category(self, ml_pkgs_df, gpu_pkgs_df):
        ml_pkgs = ml_pkgs_df[["name", "type", "version"]]
        gpu_pkgs = gpu_pkgs_df[["name", "type", "version"]]
        self.pkg_bloat_degrees["category"] = "Generic"
        for p in list(ml_pkgs.itertuples(index=False, name=None)):
            if p in self.pkg_bloat_degrees.index:
                self.pkg_bloat_degrees.loc[p, "category"] = "ML"
        for p in list(gpu_pkgs.itertuples(index=False, name=None)):
            if p in self.pkg_bloat_degrees.index:
                self.pkg_bloat_degrees.loc[p, "category"] = "GPU"

    def vul_analysis(self, cmd, working_dir="/home/ubuntu/projects/20220510/vuls"):
        cc = ContainerCreator(working_dir)
        original_report, cve_by_pkg = cc.analyze_original_container(self.image, cmd)
        self.original_cve_report = original_report
        cve_by_pkg_agg = {}
        for i in range(len(cve_by_pkg["pkg_name"])):
            pkg_name = cve_by_pkg["pkg_name"][i]
            # pkg_type = cve_by_pkg['pkg_type'][i]
            severity = cve_by_pkg["severity"][i]
            key = (pkg_name, cve_by_pkg["pkg_version"][i])
            if key not in cve_by_pkg_agg:
                cve_by_pkg_agg[key] = {
                    CRITICAL: 0,
                    HIGH: 0,
                    MEDIUM: 0,
                    LOW: 0,
                    NEGLIBILE: 0,
                }
            cve_by_pkg_agg[key][severity] = cve_by_pkg_agg[key][severity] + 1
        for n in cve_by_pkg_agg.keys():
            for i in self.pkg_bloat_degrees.index:
                if n[0] == i[0] and n[1] == i[2]:
                    severities = cve_by_pkg_agg[n]
                    for s, c in severities.items():
                        self.pkg_bloat_degrees.loc[i, s] = c

    def get_used_files(self, pkg_name, pkg_type, pkg_version):
        # these file paths are dispaly file paths, it can be like this:
        # '/usr/local/lib/python3.8/dist-packages/../../../bin/tqdm'
        all_pkg_files = set(
            self.package_files_df.groupby(
                ["package", "package_type", "version"]
            ).get_group((pkg_name, pkg_type, pkg_version))["path"]
        )

        # These file paths are real paths, like this: /usr/local/bin/tqdm
        debloated_pkg_files = set(
            self.debloated_pkg_files_df.groupby(["package", "package_type", "version"])
            .get_group((pkg_name, pkg_type, pkg_version))
            .index
        )

        used_files_set = all_pkg_files - debloated_pkg_files
        path_as_index = self.package_files_df.set_index("path")
        used_pkg_files = []
        for f in used_files_set:
            size = path_as_index.loc[f]["size(KB)"]
            used_pkg_files.append(PkgFile(f, size))

        return set(used_pkg_files)
