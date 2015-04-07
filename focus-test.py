#!/usr/bin/env python

# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#  http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY 
# KIND, either express or implied.  See the License for the 
# specific language governing permissions and limitations
# under the License.

import time
from c6d import Canon6DConnector

# callback when a camera is connected
def camera_main(camera):
    print 'camera_main', camera.guid
    
    camera.set_config('capture', 1)
    camera.set_config('capturetarget', 'Memory card')
    camera.set_config('reviewtime', 'None')
    camera.set_config('drivemode', 'Single')

    # switch on live view (required to use manualfocusdrive)
    camera.set_config('output', 1)
    time.sleep(1.0)
    
    # "quickly" step the focus toward the near end
    for i in range(10):
        result = camera.set_config('manualfocusdrive', 'Near 3')
        print result
        time.sleep(1.0)

    # more slowly step focus to far end
    for i in range(100):
        result = camera.set_config('manualfocusdrive', 'Far 2')
        print result
        time.sleep(0.5)

    # turn off live view
    camera.set_config('output', 0)
    time.sleep(1.0)

# main
connector = Canon6DConnector(camera_main)
connector.run()
