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
    
    apertures = camera.get_config_choices('aperture')

    for aperture in apertures:
        print aperture
        
        result = camera.set_config('aperture', aperture)
        print 'aperture', result
        
        result = camera.set_config('eosremoterelease', 'Press Full')
        print 'press', result
        time.sleep(0.3)

        result = camera.set_config('eosremoterelease', 'Release Full')
        print 'release', result
        time.sleep(2.0)

# main
connector = Canon6DConnector(camera_main)
connector.run()
