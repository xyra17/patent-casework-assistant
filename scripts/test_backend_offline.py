"""Run regressions with a socket barrier: no external API can be reached."""
import atexit,ipaddress,json,os,runpy,socket,sys,tempfile
from pathlib import Path
root=Path(__file__).resolve().parents[1]
blocked=[];original=socket.socket.connect

def connect(sock,address):
    if isinstance(address,tuple):
        try:local=ipaddress.ip_address(address[0]).is_loopback
        except ValueError:local=address[0]=='localhost'
        if not local:
            blocked.append({'host':address[0],'port':address[1]})
            raise OSError('offline regression: external connections disabled')
    return original(sock,address)

if __name__=='__main__':
    socket.socket.connect=connect
    log=os.getenv('OFFLINE_NETWORK_AUDIT')
    if log:atexit.register(lambda:Path(log).write_text(json.dumps({'external_connections_blocked':blocked,'guard_enabled':True},ensure_ascii=False)))
    with tempfile.TemporaryDirectory(prefix='precise-offline-') as tmp:
        os.environ.update(APP_DB_PATH=str(Path(tmp)/'app.db'),CASEWORK_DB_PATH=str(Path(tmp)/'casework.db'),
            SEMANTIC_MODEL_PATH=str(root/'tests/fixtures/legacy_semantic.yaml'),CASEWORK_EMBEDDING='0',CASEWORK_MYSQL_MIRROR='0',CASEWORK_SYNC_INTERVAL='0',DATA_MODE='demo')
        sys.path.insert(0,str(root));sys.argv=['unittest',*(sys.argv[1:] or ['discover','-s','tests','-q'])]
        runpy.run_module('unittest',run_name='__main__')
