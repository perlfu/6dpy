#!/usr/bin/env python

import ctypes
import re, select, socket, sys
import threading
import pybonjour

DEBUG = False
DLLs = ['libgphoto2.so.6', 'libgphoto2.6.dylib']

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
gphoto.gp_abilities_list_lookup_model.argtypes = [ ctypes.c_void_p, ctypes.c_char_p ]
gphoto.gp_result_as_string.restype = ctypes.c_char_p
gphoto.gp_log_add_func.argtypes = [ ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p ]
gphoto.gp_setting_set.argtypes = [ ctypes.c_char_p, ctypes.c_char_p, ctypes.c_char_p ]
gphoto.gp_camera_set_abilities.argtypes = [ ctypes.c_void_p, ctypes.Structure ]

class CameraAbilities(ctypes.Structure):
    _fields_ = [('model', (ctypes.c_char * 128)), ('data', (ctypes.c_char * 4096))]

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

class PTPIPCamera:
    def __init__(self, target, guid):
        self.context = gphoto.gp_context_new()
        self.target = target
        self.guid = guid
        self.handle = ctypes.c_void_p()
        self.portlist = None
        self.abilitylist = None
        self.connected = False

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
        print 'allocate camera'
        res = gphoto.gp_camera_new(ctypes.pointer(self.handle))
        gphoto_check(res)
      
        # set model and guid in settings file
        gphoto.gp_setting_set("gphoto2", "model", "PTP/IP Camera")
        gphoto.gp_setting_set("ptp2_ip", "guid", self.encoded_guid())
        
        # load abilities
        if not self.abilitylist:
            print 'load abilities list'
            self.abilitylist = ctypes.c_void_p()
            gphoto.gp_abilities_list_new(ctypes.pointer(self.abilitylist))
            res = gphoto.gp_abilities_list_load(self.abilitylist)
            gphoto_check(res)
        
        # search for model abilities
        print 'search abilities list'
        index = gphoto.gp_abilities_list_lookup_model(self.abilitylist, 'PTP/IP Camera')
        gphoto_check(index)
        print 'found at', index
        
        # load abilities
        print 'load abilities'
        abilities = CameraAbilities()
        res = gphoto.gp_abilities_list_get_abilities(self.abilitylist, index, ctypes.pointer(abilities))
        gphoto_check(res)

        # set camera abilities
        print 'set camera abilities'
        res = gphoto.gp_camera_set_abilities(self.handle, abilities)
        gphoto_check(res)

        # load port list
        if not self.portlist:
            print 'load port list'
            self.portlist = ctypes.c_void_p()
            gphoto.gp_port_info_list_new(ctypes.pointer(self.portlist))
            res = gphoto.gp_port_info_list_load(self.portlist)
            gphoto_check(res)

        # find port info entry
        print 'search for port info'
        index = gphoto.gp_port_info_list_lookup_path(self.portlist, self.encoded_path())
        gphoto_check(index)
        print 'found at', index

        # load port info entry
        print 'load port info'
        info = ctypes.c_void_p()
        res = gphoto.gp_port_info_list_get_info(self.portlist, index, ctypes.pointer(info))
        gphoto_check(res)

        # set the camera with the appropriate port info
        print 'set camera port'
        res = gphoto.gp_camera_set_port_info(self.handle, info)
        gphoto_check(res)
        
        # load the port path for debugging
        if DEBUG:
            path = ctypes.c_char_p()
            res = gphoto.gp_port_info_get_path(info, ctypes.pointer(path))
            gphoto_check(res)
            print path.value

        # connect to camera
        print 'connecting...'
        res = gphoto.gp_camera_init(self.handle, self.context)
        gphoto_check(res)
        print 'connected.'

        self.connected = True
        return True

    def disconnect(self):
        pass
       
class MDNSListener:
    def __init__(self, callback=None):
        self.timeout = 5
        self.callback = callback
        self.shutdown = False

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
            print 'query', hosttarget

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
        print 'resolve', serviceName

        try:
            ready = select.select([resolve_sdRef], [], [], self.timeout)
            if resolve_sdRef in ready[0]:
                pybonjour.DNSServiceProcessResult(resolve_sdRef)
        finally:
            resolve_sdRef.close()

    def search(self):
        def callback(sdRef, flags, interfaceIndex, errorCode, serviceName, regtype, replyDomain):
            self.browse_callback(sdRef, flags, interfaceIndex, errorCode, serviceName, regtype, replyDomain)

        browse_sdRef = pybonjour.DNSServiceBrowse(regtype = "_ptp._tcp", callBack = callback)
        try:
            while not self.shutdown:
                print 'searching...'
                ready = select.select([browse_sdRef], [], [], self.timeout)
                if browse_sdRef in ready[0]:
                    pybonjour.DNSServiceProcessResult(browse_sdRef)
        finally:
            browse_sdRef.close()

    def start(self):
        def run():
            self.search()
        self.thread = threading.Thread(target=run)
        self.thread.start()
        print 'MDNSListener', 'started'

    def join(self, timeout=None):
        if not self.thread.isAlive():
            pass
        elif timeout:
            self.thread.join(timeout=timeout)
        else:
            self.thread.join()
        return not self.thread.isAlive()

class Canon6DConnection:
    def __init__(self, ip, guid, callback):
        self.ip = ip
        self.guid = guid
        self.callback = callback

    def connect(self):
        print 'Canon6DConnection started to %s (%s)' % (self.ip, self.guid)
        self.camera = PTPIPCamera(self.ip, self.guid)
        try:
            self.camera.connect()
            print 'Canon6DConnection connected to %s (%s)' % (self.ip, self.guid)
            self.callback(self.camera)
        except Exception as e:
            print 'Canon6DConnection failed for %s (%s) - %s' % (self.ip, self.guid, str(e))

    def start(self):
        def run():
            self.connect()
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

class Canon6DConnector:
    def __init__(self, callback):
        self.callback = callback
        self.connections = []

    def connect(self, ip, guid):
        connection = Canon6DConnection(ip, guid, callback)
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
        print 'shutdown'
        if mdns:
            mdns.shutdown = True
        for c in self.connections:
            c.shutdown()
        sys.exit(0)

def camera_main(camera):
    pass

def main(args):
    connector = Canon6DConnector(camera_main)
    connector.run()
    #ptp = PTPIPCamera('192.168.16.22', 'AF90AA13-9344-46EE-9751-ED6797E19F06')
    #ptp.connect()

if __name__ == "__main__":
    main(sys.argv[1:])
