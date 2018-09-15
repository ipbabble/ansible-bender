"""
Make sure that we can build using buildah builder.
"""
import os
import re
import random
import string
import subprocess

import pytest

from ansible_bender.builders.buildah_builder import buildah, inspect_buildah_resource, podman_run_cmd

this_dir = os.path.dirname(os.path.abspath(__file__))
tests_dir = os.path.dirname(this_dir)
project_dir = os.path.dirname(tests_dir)
data_dir = os.path.join(tests_dir, "data")
basic_playbook_path = os.path.join(data_dir, "basic_playbook.yaml")
bad_playbook_path = os.path.join(data_dir, "bad_playbook.yaml")
base_image = "docker.io/library/python:3-alpine"


@pytest.fixture()
def target_image():
    im = "registry.example.com/ab-test-" + random_word(12) + ":oldest"
    yield im
    try:
        buildah("rmi", [im])
    except subprocess.CalledProcessError:
        pass


def random_word(length):
    # https://stackoverflow.com/a/2030081/909579
    letters = string.ascii_lowercase
    return ''.join(random.choice(letters) for _ in range(length))


def ab(args, debug=False, return_output=False, ignore_result=False):
    """
    python3 -m ab.cli -v build ./playbook.yaml registry.fedoraproject.org/fedora:28 asdqwe-image

    :return:
    """
    cmd = ["python3", "-m", "ansible_bender.cli"]
    if debug:
        cmd += ["--debug"]
    if ignore_result:
        return subprocess.call(cmd + args, cwd=project_dir)
    if return_output:
        return subprocess.check_output(cmd + args, cwd=project_dir, universal_newlines=True)
    else:
        # don't use run_cmd here, it makes things complicated
        subprocess.check_call(cmd + args, cwd=project_dir)


def test_output(target_image):
    cmd = ["build", basic_playbook_path, base_image, target_image]
    out = ab(cmd, return_output=True, debug=False)
    assert 'Getting image source signatures' in out
    assert not re.match(r'ERROR\s+Getting image source signatures', out)
    assert 'Copying ' in out
    assert not re.match(r'ERROR\s+Copying ', out)
    buildah("inspect", ["-t", "image", target_image])


def test_build_basic_image(target_image):
    cmd = ["build", basic_playbook_path, base_image, target_image]
    ab(cmd)
    buildah("inspect", ["-t", "image", target_image])


def test_build_basic_image_with_env_vars(target_image):
    a_b = "A=B"
    x_y = "X=Y"
    cmd = ["build", "-e", a_b, x_y, "--",
           basic_playbook_path, base_image, target_image]
    ab(cmd)
    out = inspect_buildah_resource("image", target_image)
    assert a_b in out["OCIv1"]["config"]["Env"]
    assert x_y in out["OCIv1"]["config"]["Env"]
    e = podman_run_cmd(target_image, ["env"], return_output=True)
    assert a_b in e
    assert x_y in e


def test_build_basic_image_with_wrong_env_vars(target_image):
    la_la_la = "AB-LA-BLA-LA-LA-BLA"
    cmd = ["build", "-e", la_la_la, "--", basic_playbook_path, base_image, target_image]
    with pytest.raises(subprocess.CalledProcessError) as exc:
        ab(cmd)
        e = ("There was an error during execution: "
             "Environment variable {} doesn't seem to be "
             "specified in format 'KEY=VALUE'.".format(la_la_la))
        assert e in exc.value.message


def test_build_basic_image_with_labels(target_image):
    a_b = "A=B"
    x_y = "x=y"
    cmd = ["build", "-l", a_b, x_y, "--",
           basic_playbook_path, base_image, target_image]
    ab(cmd)
    out = inspect_buildah_resource("image", target_image)
    assert out["OCIv1"]["config"]["Labels"]["A"] == "B"
    assert out["OCIv1"]["config"]["Labels"]["x"] == "y"


def test_build_basic_image_with_build_volumes(tmpdir, target_image):
    real_tmp = str(tmpdir)
    with open(os.path.join(real_tmp, "file.txt"), "w") as fd:
        fd.write("Hello, hello!")
    container_mount = "/asdqwe"
    vol_spec = "%s:%s" % (real_tmp, container_mount)
    cmd = ["build", "--build-volumes", vol_spec, "--",
           basic_playbook_path, base_image, target_image]
    ab(cmd)


def test_build_basic_image_with_all_params(target_image):
    workdir_path = "/etc"
    l_a_b = "A=B"
    l_x_y = "x=y"
    e_a_b = "A=B"
    e_x_y = "X=Y"
    cmd, cmd_e = "ls -lha", ["ls", "-lha"]
    user = "1000123"
    p_80, p_443 = "80", "443"
    runtime_volume = "/var/lib/asdqwe"
    cmd = ["build",
           "-w", workdir_path,
           "-l", l_a_b, l_x_y,
           "-e", e_a_b, e_x_y,
           "--cmd", cmd,
           "-u", user,
           "-p", p_80, p_443,
           "--runtime-volumes", runtime_volume,
           "--",
           basic_playbook_path, base_image, target_image]
    ab(cmd)
    out = inspect_buildah_resource("image", target_image)
    co = out["OCIv1"]["config"]
    assert co["WorkingDir"] == workdir_path
    assert co["Labels"]["A"] == "B"
    assert co["Labels"]["x"] == "y"
    assert e_a_b in co["Env"]
    assert e_x_y in co["Env"]
    assert co["Cmd"] == cmd_e
    assert co["User"] == user
    assert p_80 in co["ExposedPorts"]
    assert p_443 in co["ExposedPorts"]
    # assert runtime_volume in co["Volumes"]  # FIXME: figure out how to check this


def test_build_failure():
    target_image = "registry.example.com/ab-test-" + random_word(12) + ":oldest"
    target_failed_image = target_image + "-failed"
    cmd = ["build", bad_playbook_path, base_image, target_image]
    with pytest.raises(subprocess.CalledProcessError):
        ab(cmd)
    buildah("inspect", ["-t", "image", target_failed_image])
    buildah("rmi", [target_failed_image])


def test_two_runs(target_image):
    """ this is a naive test to verify race condition """
    cmd = ["python3", "-m", "ansible_bender.cli", "build", basic_playbook_path, base_image,
           target_image]
    p1 = subprocess.Popen(cmd)
    p2 = subprocess.Popen(cmd)
    p1.wait()
    p2.wait()
    assert p1.returncode == 0
    assert p2.returncode == 0


def test_buildah_err_output(capfd):
    cmd = ["build", basic_playbook_path, base_image, "vrerv\\23&^&4//5B/F/BSFD/B"]
    ab(cmd, debug=False, ignore_result=True)
    c = capfd.readouterr()
    assert "error parsing target image name" in c.err
    assert "Invalid image name" in c.err
    assert "was an error during" in c.err
