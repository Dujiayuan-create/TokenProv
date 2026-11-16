package policy

import future.keywords.every

default nv_match := false

nv_match {
    count(input) > 0
    every device in input {
        device["x-nvidia-device-type"] == "gpu"
        device.measres == "success"
        device.secboot == true
        device.dbgstat == "disabled"
    }
}
