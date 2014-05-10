#!/usr/bin/env python

import time
from c6d import Canon6DConnector

# callback when a camera is connected
def camera_main(camera):
    print 'camera_main', camera.guid
    
    camera.set_config('capture', 1)
    camera.set_config('capturetarget', 'Memory card')
    
    apertures = camera.get_config_choices('aperture')

    for aperture in apertures:
        print aperture
        result = camera.set_config('aperture', aperture)
        print 'aperture', result
        result = camera.set_config('eosremoterelease', 'Press 3')
        print 'press', result
        time.sleep(0.5)
        result = camera.set_config('eosremoterelease', 'Release 3')
        print 'release', result
        time.sleep(2.0)

# main
connector = Canon6DConnector(camera_main)
connector.run()
