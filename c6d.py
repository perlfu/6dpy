#!/usr/bin/env python

import ctypes
import re, select, socket, sys
import threading
import time
import pybonjour

DEBUG = False
DLLs = ['libgphoto2.so.6', 'libgphoto2.6.dylib']

GP_CAPTURE_IMAGE            = 0
GP_CAPTURE_MOVIE            = 1
GP_CAPTURE_SOUND            = 2

GP_EVENT_UNKNOWN            = 0
GP_EVENT_TIMEOUT            = 1
GP_EVENT_FILE_ADDED         = 2
GP_EVENT_FOLDER_ADDED       = 3
GP_EVENT_CAPTURE_COMPLETE   = 4

gphoto = None
for dll in DLLs:
    if not gphoto:
        try:
            gphoto = ctypes.CDLL(dll)
        except:
            pass

if not gphoto:
    raise Exception('could not locate gphoto2 dynamic library')
    
gphoto.gp_context_new.restype = ctypes.c_void_p
gphoto.gp_camera_init.argtypes = [ ctypes.c_void_p, ctypes.c_void_p ]
gphoto.gp_context_unref.argtypes = [ ctypes.c_void_p ]
gphoto.gp_abilities_list_lookup_model.argtypes = [ ctypes.c_void_p, ctypes.c_char_p ]
gphoto.gp_result_as_string.restype = ctypes.c_char_p
gphoto.gp_log_add_func.argtypes = [ ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p ]
gphoto.gp_setting_set.argtypes = [ ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p ]
gphoto.gp_camera_set_abilities.argtypes = [ ctypes.c_void_p, ctypes.Structure ]

class CameraAbilities(ctypes.Structure):
    _fields_ = [('model', (ctypes.c_char * 128)), ('data', (ctypes.c_char * 4096))]

class CameraFilePath(ctypes.Structure):
    _fields_ = [('name', (ctypes.c_char * 128)), ('folder', (ctypes.c_char * 1024))]

class GPhotoError(Exception):
    def __init__(self, result, message):
        self.result = result
        self.message = message
    def __str__(self):
        return self.message + ' (' + str(self.result) + ')'

def gphoto_debug(level, domain, msg, data):
    print domain, msg
    return 0

GPhotoLogFunc = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_int, ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p)
GPhotoDebug = GPhotoLogFunc(gphoto_debug)
if DEBUG:
    gphoto.gp_log_add_func(2, GPhotoDebug, 0)

def gphoto_check(result):
    if result < 0:
        message = gphoto.gp_result_as_string(result)
        raise GPhotoError(result, message)
    return result

class Common:
    log_label = 'Common'

    def log(self, msg, debug=False):
        if (debug and DEBUG) or (not debug):
            print self.log_label, msg

    def debug(self, msg):
        self.log(msg, debug=True)
    
    def start(self):
        def run():
            self.log('started thread')
            self.run()
            self.log('finished thread')
        self.log('starting thread')
        self.thread = threading.Thread(target=run)
        self.thread.start()
    
    def join(self, timeout=None):
        if not self.thread.isAlive():
            pass
        elif timeout:
            self.thread.join(timeout=timeout)
        else:
            self.thread.join()
        return not self.thread.isAlive()

    def shutdown(self):
        pass

class PTPIPCamera(Common):
    log_label = 'PTPIPCamera'

    def __init__(self, target, guid):
        self.context = ctypes.c_void_p() #gphoto.gp_context_new()
        self.target = target
        self.guid = guid
        self.handle = ctypes.c_void_p()
        self.portlist = None
        self.abilitylist = None
        self.connected = False
        self.cached_root = None
        self.cached_time = 0
        self.cache_expiry = 2 # seconds

    def encoded_path(self):
        return "ptpip:" + self.target

    def encoded_guid(self):
        tmp = self.guid.split("-")
        guid = []
        l = lambda s: [ s[i:i+2:] for i in xrange(0,len(s),2) ][::-1]
        for i in xrange(0,3):
            guid += l(tmp[i])
        guid += tmp[3]
        guid += tmp[4]
        tmp = "".join(guid).lower()
        guid = []
        for i in range(0, len(tmp), 2):
            guid.append(tmp[i:i+2])
        guid = ":".join(guid)
        
        return guid

    def connect(self):
        # allocate and initialise a new camera
        self.debug('allocate camera')
        res = gphoto.gp_camera_new(ctypes.pointer(self.handle))
        gphoto_check(res)
      
        # set model and guid in settings file
        gphoto.gp_setting_set("gphoto2", "model", "PTP/IP Camera")
        gphoto.gp_setting_set("ptp2_ip", "guid", self.encoded_guid())
        
        # load abilities
        if not self.abilitylist:
            self.debug('load abilities list')
            self.abilitylist = ctypes.c_void_p()
            gphoto.gp_abilities_list_new(ctypes.pointer(self.abilitylist))
            res = gphoto.gp_abilities_list_load(self.abilitylist)
            gphoto_check(res)
        
        # search for model abilities
        self.debug('search abilities list')
        index = gphoto.gp_abilities_list_lookup_model(self.abilitylist, 'PTP/IP Camera')
        gphoto_check(index)
        self.debug('found at %d' % index)
        
        # load abilities
        self.debug('load abilities')
        abilities = CameraAbilities()
        res = gphoto.gp_abilities_list_get_abilities(self.abilitylist, index, ctypes.pointer(abilities))
        gphoto_check(res)

        # set camera abilities
        self.debug('set camera abilities')
        res = gphoto.gp_camera_set_abilities(self.handle, abilities)
        gphoto_check(res)

        # load port list
        if not self.portlist:
            self.debug('load port list')
            self.portlist = ctypes.c_void_p()
            gphoto.gp_port_info_list_new(ctypes.pointer(self.portlist))
            res = gphoto.gp_port_info_list_load(self.portlist)
            gphoto_check(res)

        # find port info entry
        self.debug('search for port info')
        index = gphoto.gp_port_info_list_lookup_path(self.portlist, self.encoded_path())
        gphoto_check(index)
        self.debug('found at %d' % index)

        # load port info entry
        self.debug('load port info')
        info = ctypes.c_void_p()
        res = gphoto.gp_port_info_list_get_info(self.portlist, index, ctypes.pointer(info))
        gphoto_check(res)

        # set the camera with the appropriate port info
        self.debug('set camera port')
        res = gphoto.gp_camera_set_port_info(self.handle, info)
        gphoto_check(res)
        
        # load the port path for debugging
        if DEBUG:
            path = ctypes.c_char_p()
            res = gphoto.gp_port_info_get_path(info, ctypes.pointer(path))
            gphoto_check(res)
            self.debug(path.value)

        # connect to camera
        self.log('connecting...')
        res = gphoto.gp_camera_init(self.handle, self.context)
        gphoto_check(res)
        self.log('connected.')

        self.connected = True
        return True

    def disconnect(self):
        self._clear_cache()
        res = gphoto.gp_camera_exit(self.handle, self.context)
        gphoto_check(res)
        res = gphoto.gp_camera_unref(self.handle)
        gphoto_check(res)
        res = gphoto.gp_context_unref(self.context)
        gphoto_check(res)
        # FIXME: gphoto PTP/IP does not close sockets properly; try to work around?

    def _root_widget(self):
        now = time.time()
        if (not self.cached_root) or abs(now - self.cached_time) > self.cache_expiry:
            if not self.cached_root:
                gphoto.gp_widget_free(self.cached_root)
                self.cached_root = None
            root = ctypes.c_void_p()
            res = gphoto.gp_camera_get_config(self.handle, ctypes.pointer(root), self.context)
            if res >= 0:
                self.cached_root = root
                self.cached_time = now
        return self.cached_root

    def _clear_cache(self):
        if self.cached_root:
            gphoto.gp_widget_free(self.cached_root)
            self.cached_root = None

    def _find_widget(self, label):
        root = self._root_widget()
        if root:
            child = ctypes.c_void_p()
            res = gphoto.gp_widget_get_child_by_name(root, ctypes.c_char_p(label), ctypes.pointer(child))
            if res >= 0:
                return (root, child)
        return None

    widget_types = { 0: 'window',
                     1: 'section',
                     2: 'text',
                     3: 'range',
                     4: 'toggle',
                     5: 'radio',
                     6: 'menu',
                     7: 'button',
                     8: 'date' }

    def _widget_type(self, pair):
        (root, child) = pair
        w_type = ctypes.c_int()
        res = gphoto.gp_widget_get_type(child, ctypes.pointer(w_type))
        gphoto_check(res)
        w_type = w_type.value
        if w_type in self.widget_types:
            return self.widget_types[w_type]
        else:
            return 'unknown'

    def _widget_value(self, pair):
        (root, child) = pair
        w_type = self._widget_type(pair)
        if w_type == 'text' or w_type == 'menu' or w_type == 'radio':
            ptr = ctypes.c_char_p()
            res = gphoto.gp_widget_get_value(child, ctypes.pointer(ptr))
            gphoto_check(res)
            return (w_type, ptr.value)
        elif w_type == 'range':
            top = ctypes.c_float()
            bottom = ctypes.c_float()
            step = ctypes.c_float()
            value = ctypes.c_float()
            res = gphoto.gp_widget_get_range(child, ctypes.pointer(bottom), ctypes.pointer(top), ctypes.pointer(step))
            gphoto_check(res)
            res = gphoto.gp_widget_get_value(child, ctypes.pointer(value))
            gphoto_check(res)
            return (w_type, value.value, bottom.value, top.value, step.value)
        elif w_type == 'toggle' or w_type == 'date':
            value = ctypes.c_int()
            res = gphoto.gp_widget_get_value(child, ctypes.pointer(value))
            gphoto_check(res)
            return (w_type, value.value)
        else:
            return None
    
    def _match_choice(self, pair, value):
        choices = self._widget_choices(pair)
        if isinstance(value, int):
            if (value >= 0) and (value < len(choices)):
                return choices[value]
        for (i, c) in zip(range(len(choices)), choices):
            try:
                if c == str(value):
                    return c
                elif float(c) == float(value):
                    return c
                elif int(c) == int(value):
                    return c
            except:
                pass
        if isinstance(value, str):
            return value
        else:
            return str(value)

    def _widget_set(self, pair, value):
        (root, child) = pair
        w_type = self._widget_type(pair)
        if w_type == 'toggle':
            if value:
                value = 1
            else:
                value = 0
        elif w_type == 'range':
            value = float(value)
        elif (w_type == 'radio') or (w_type == 'menu'):
            value = self._match_choice(pair, value)

        if isinstance(value, int):
            v = ctypes.c_int(value)
            res = gphoto.gp_widget_set_value(child, ctypes.pointer(v))
            return (res >= 0)
        elif isinstance(value, float):
            v = ctypes.c_float(float(value))
            res = gphoto.gp_widget_set_value(child, ctypes.pointer(v))
            return (res >= 0)
        elif isinstance(value, str):
            v = ctypes.c_char_p(value)
            res = gphoto.gp_widget_set_value(child, v)
            return (res >= 0)
        else:
            return False

    def _widget_choices(self, pair):
        (root, child) = pair
        w_type = self._widget_type(pair)
        if w_type == 'radio' or w_type == 'menu':
            count = gphoto.gp_widget_count_choices(child)
            if count > 0:
                choices = []
                for i in range(count):
                    ptr = ctypes.c_char_p()
                    res = gphoto.gp_widget_get_choice(child, i, ctypes.pointer(ptr))
                    gphoto_check(res)
                    choices.append(ptr.value)
                return choices
        return None

    def get_config(self, label):
        pair = self._find_widget(label)
        value = None
        if pair:
            value = self._widget_value(pair)
        return value

    def get_config_choices(self, label):
        pair = self._find_widget(label)
        value = None
        if pair:
            value = self._widget_choices(pair)
        return value

    def set_config(self, label, value):
        pair = self._find_widget(label)
        result = False
        if pair:
            result = self._widget_set(pair, value)
            if result:
                res = gphoto.gp_camera_set_config(self.handle, pair[0], self.context)
                result = (res >= 0)
        return result

    known_widgets = [
        'uilock',
        'bulb',
        'drivemode',
        'focusmode',
        'autofocusdrive',
        'manualfocusdrive',
        'eoszoom',
        'eoszoomposition',
        'eosviewfinder',
        'eosremoterelease',
        'serialnumber',
        'manufacturer',
        'cameramodel',
        'deviceversion',
        'model',
        'batterylevel',
        'lensname',
        'eosserialnumber',
        'shuttercounter',
        'availableshots',
        'reviewtime',
        'output',
        'evfmode',
        'ownername',
        'artist',
        'copyright',
        'autopoweroff',
        'imageformat',
        'imageformatsd',
        'iso',
        'whitebalance',
        'colortemperature',
        'whitebalanceadjusta',
        'whitebalanceadjustb',
        'whitebalancexa',
        'whitebalancexb',
        'colorspace'
        'exposurecompensation',
        'focusmode',
        'autoexposuremode',
        'picturestyle',
        'shutterspeed',
        'bracketmode',
        'aeb',
        'aperture',
        'capturetarget' ]

    def list_config(self):
        config = {}
        for k in self.known_widgets:
            config[k] = self.get_config(k)
        return config

    # XXX: this hangs waiting for response from camera
    def trigger_capture(self):
        res = gphoto.gp_camera_trigger_capture(self.handle, self.context)
        try:
            gphoto_check(res)
            return True
        except GPhotoError as e:
            self.log(str(e))
            return False

    # XXX: this hangs waiting for response from camera
    def capture(self, capture_type=GP_CAPTURE_IMAGE):
        path = CameraFilePath()
        res = gphoto.gp_camera_capture(self.handle, ctypes.c_int(capture_type), ctypes.pointer(path), self.context)
        try:
            gphoto_check(res)
            return (path.folder, path.name)
        except GPhotoError as e:
            self.log(str(e))
            return None

    def wait_for_event(self, timeout=10):
        ev_type = ctypes.c_int()
        data = ctypes.c_void_p()
        res = gphoto.gp_camera_capture(self.handle, 
                ctypes.c_int(timeout),
                ctypes.pointer(ev_type),
                ctypes.pointer(data), self.context)
        try:
            gphoto_check(res)
            return ev_type.value
        except GPhotoError as e:
            self.log(str(e))
            return None

class MDNSListener(Common):
    log_label = 'MDNSListener'

    def __init__(self, callback=None):
        self.timeout = 5
        self.callback = callback
        self._shutdown = False

    def notify(self, ip, guid):
        if self.callback:
            self.callback(ip, guid)

    def resolve_callback(self, 
                        sdRef, flags, interfaceIndex, errorCode, fullname,
                        hosttarget, port, txtRecord):
        def callback(sdRef, flags, interfaceIndex, errorCode, fullname,
                            rrtype, rrclass, rdata, ttl):
            if errorCode == pybonjour.kDNSServiceErr_NoError:
                ip = socket.inet_ntoa(rdata)
                m = re.search(r'tid.canon.com=([0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12})', txtRecord)
                if m:
                    guid = m.group(1)
                    self.notify(ip, guid)

        if errorCode == pybonjour.kDNSServiceErr_NoError:
            query_sdRef = pybonjour.DNSServiceQueryRecord(
                    interfaceIndex = interfaceIndex,
                    fullname = hosttarget,
                    rrtype = pybonjour.kDNSServiceType_A,
                    callBack = callback)
            self.log('query %s' % hosttarget)

            try:
                ready = select.select([query_sdRef], [], [], self.timeout)
                if query_sdRef in ready[0]:
                    pybonjour.DNSServiceProcessResult(query_sdRef)
            finally:
                query_sdRef.close()

    def browse_callback(self,
                        sdRef, flags, interfaceIndex, errorCode, serviceName,
                        regtype, replyDomain):
        if errorCode != pybonjour.kDNSServiceErr_NoError:
            return
        if not (flags & pybonjour.kDNSServiceFlagsAdd):
            return

        def callback(sdRef, flags, interfaceIndex, errorCode, fullname, hosttarget, port, txtRecord):
            self.resolve_callback(sdRef, flags, interfaceIndex, errorCode, fullname, hosttarget, port, txtRecord)
        
        resolve_sdRef = pybonjour.DNSServiceResolve(0,
                interfaceIndex,
                serviceName,
                regtype,
                replyDomain,
                callback)
        self.log('resolve %s' % serviceName)

        try:
            ready = select.select([resolve_sdRef], [], [], self.timeout)
            if resolve_sdRef in ready[0]:
                pybonjour.DNSServiceProcessResult(resolve_sdRef)
        finally:
            resolve_sdRef.close()

    def run(self):
        def callback(sdRef, flags, interfaceIndex, errorCode, serviceName, regtype, replyDomain):
            self.browse_callback(sdRef, flags, interfaceIndex, errorCode, serviceName, regtype, replyDomain)
        
        self.log('started')

        self.browse_sdRef = pybonjour.DNSServiceBrowse(regtype = "_ptp._tcp", callBack = callback)
        try:
            while not self._shutdown:
                if DEBUG:
                    self.log('searching...')
                ready = select.select([self.browse_sdRef], [], [], self.timeout)
                if (not self._shutdown) and (self.browse_sdRef in ready[0]):
                    pybonjour.DNSServiceProcessResult(self.browse_sdRef)
        except select.error as e:
            # happens if socket closed, i.e. shutdown
            pass
        finally:
            # tidy up if shutdown has not been invoked
            if not self._shutdown:
                self.browse_sdRef.close()

        self.log('shutdown')

    def shutdown(self):
        # signal shutdown and force close socket
        self.log('signalling shutdown')
        self._shutdown = True
        self.browse_sdRef.close()

class Canon6DConnection(Common):
    log_label = 'Canon6DConnection'

    def __init__(self, ip, guid, callback):
        self.ip = ip
        self.guid = guid
        self.callback = callback

    def run(self):
        self.log('started %s (%s)' % (self.ip, self.guid))
        self.camera = PTPIPCamera(self.ip, self.guid)
        try:
            self.camera.connect()
            self.log('connected to %s (%s)' % (self.ip, self.guid))
            self.callback(self.camera)
        except Exception as e:
            self.log('failed for %s (%s) - %s' % (self.ip, self.guid, str(e)))
        finally:
            try:
                self.camera.disconnect()
            except:
                pass
        self.log('shutdown %s (%s)' % (self.ip, self.guid))

class Canon6DConnector:
    def __init__(self, callback):
        self.callback = callback
        self.connections = []

    def connect(self, ip, guid):
        if len(self.connections) == 0:
            connection = Canon6DConnection(ip, guid, self.callback)
            connection.start()
            self.connections.append(connection)

    def run(self):
        def callback(ip, guid):
            self.connect(ip, guid)
        
        # start up
        mdns = MDNSListener(callback=callback)
        mdns.start()

        # monitor
        shutdown = False
        while (not shutdown) and (mdns or (len(self.connections) > 0)):
            if mdns:
                try:
                    if mdns.join(timeout=1.0):
                        mdns = None
                except:
                    shutdown = True
            to_scan = self.connections[:]
            for c in to_scan:
                try:
                    if c.join(timeout=1.0):
                        self.connections.remove(c)
                except:
                    shutdown = True

        # shutdown
        if mdns:
            mdns.shutdown()
        for c in self.connections:
            c.shutdown()
        sys.exit(0)

def camera_main(camera):
    print 'camera_main', camera.guid
    camera.set_config('capture', 1)
    
    config = camera.list_config()
    print 'got config'
    for k in sorted(config.keys()):
        v = config[k]
        if v and (v[0] == 'radio'):
            print k, v, camera.get_config_choices(k) 
        else:
            print k, v

    result = camera.set_config('aperture', '8.0')
    print 'set aperture', result
    result = camera.set_config('capturetarget', 'Memory card')
    print 'set memory card', result
    result = camera.set_config('eosremoterelease', 'Immediate')
    print 'trigger capture', result
    time.sleep(1)

def main(args):
    connector = Canon6DConnector(camera_main)
    connector.run()

if __name__ == "__main__":
    main(sys.argv[1:])
