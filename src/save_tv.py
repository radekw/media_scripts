#!/usr/bin/python

# Copyright [2009] [Radek Wierzbicki]
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
   
import os, shutil, sys, signal, subprocess, getopt
import re, logging, urllib2, sqlite3
from ConfigParser import SafeConfigParser
from stat import *
import mechanize, ClientForm
from hashlib import sha1
from os import urandom
import prowlpy, xmpp

########################################
def usage():
    print 'Usage:'
    print '   save_tv.py'
    print 'Options:'
    print '   -q, --query            download links only'
    print '   -d, --download         download shows from existing link files'
    print '   -v, --verbose          verbose output'
    print '   -h, --help             this message'
    sys.exit(os.EX_USAGE)

########################################
class Show:
    NEW = 'new'
    DOWNLOADED = 'downloaded'
    DOWNLOADING = 'downloading'
    ERROR = 'error'
    def __init__(self, id, title, date, time, url, telecastid, size, status):
        if id == None:
            self.id = generate_unique_id('%s%s%s' % (title, date, time))
        else:
            self.id = id
        self.title = title
        self.date = date
        self.time = time
        self.url = url
        self.telecastid = telecastid
        self.size = int(size)
        self.status = status
        self.titleD = '%s.%s' % (title.replace(' ', '.'), date)
        self.filename = '%s.avi' % self.titleD
    def insert(self):
        sql = 'insert or ignore into shows '
        sql += '(id, title, date, time, url, telecastid, size, status, status_update_time) '
        sql += 'values (?, ?, ?, ?, ?, ?, ?, ?, datetime("now", "localtime"))'
        t = (self.id, self.title, self.date, self.time, 
             self.url, self.telecastid, self.size, self.status)
        _database.execute(sql, t)
        _database.commit()
    def update(self):
        sql = 'update shows '
        sql += 'set title = ?, date = ?, time = ?, '
        sql += 'url = ?, telecastid = ?, size = ?, status = ?, '
        sql += 'status_update_time = datetime("now", "localtime") '
        sql += 'where id = ?'
        t = (self.title, self.date, self.time, self.url, 
             self.telecastid, self.size, self.status, self.id)
        _database.execute(sql, t)
        _database.commit()
    def update_status(self, status):
        self.status = status
        self.update()
    
########################################
def can_i_run():
    out = subprocess.Popen(['ps', 'hx'], stdout=subprocess.PIPE).communicate()[0]
    c = 0
    process = os.path.basename(sys.argv[0])
    for o in out.split('\n'):
        if o.find('python') >= 0 and o.find(process) >= 0:
            c += 1
    if c > 1:
        return False
    return True

########################################
def generate_unique_id(text=None, l=10):
    if l > 40:
        l = 40
    if text is None:
        text = urandom(100)
    return sha1(text).hexdigest()[:l]

########################################
def connect_to_sqlite():
    f = os.path.join(_config.get('directories', 'storage'), 'save_tv.sqlite')
    c = sqlite3.connect(f)
    s = 'CREATE TABLE IF NOT EXISTS shows '
    s += '(id text primary key, title text, '
    s += 'date text, time text, url text, telecastid text, '
    s += 'size integer, status text, status_update_time text);'
    c.execute(s)
    return c
    
########################################
def fix_db():
    pass

########################################
def deumlaut(s):
    s = s.replace('\xdf', 'ss')
    s = s.replace('\xfc', 'ue')
    s = s.replace('\xdc', 'Ue')
    s = s.replace('\xf6', 'oe')
    s = s.replace('\xd6', 'Oe')
    s = s.replace('\xe4', 'ae')
    s = s.replace('\xc4', 'Ae')
    return s

########################################
def fix_filename(s):
    s = s.replace('_%s' % _config.get('login', 'username'), '')
    return s

########################################
def login():
    logger = logging.getLogger()
    br = mechanize.Browser()
    br.addheaders = [('User-agent', 'Firefox/3.0.14')]

    logger.info('logging on')
    br.open('%s/%s' % (_url_site, '/STV/S/obj/user/usShowLogin.cfm'))
    br.select_form(name='loginFrm')
    br['sUsername'] = _config.get('login', 'username')
    br['sPassword'] = _config.get('login', 'password')
    res = br.submit()
    
    return br

########################################
def query(br):
    logger = logging.getLogger()
    
    logger.info('getting show listing')
    shows = []
    br.open('%s/%s' % (_url_site, '/STV/M/obj/user/usShowVideoArchive.cfm'))
    try:
        links = br.links(url_regex=r'ShowDownload\.cfm')
    except mechanize._mechanize.LinkNotFoundError:
        logger.error('ShowDownload links not found')
        return
    for link in links:
        shows.append('%s/%s' % (_url_site, link.url))
    
    logger.info('getting show links')
    links = []
    re_tid = re.compile(r'.+TelecastID=(\d+)&.+')
    for show in shows:
        logger.debug(show)
        br.open(show)
        try:
            dl = br.find_link(url_regex=r'm=dl')
        except mechanize._mechanize.LinkNotFoundError:
            logger.error('download link not found')
            continue
        i = dl.url.find('http://')
        u = dl.url[i:-1]
        m = re_tid.match(show)
        if m:
            tid = m.group(1)
        else:
            m = ''
        links.append((u, tid))

    logger.info('getting show details')
    re_tdt = re.compile(r'(.+)_{1,2}(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2})')
    for link, tid in links:
        req = urllib2.Request(link, headers={'User-agent': 'Firefox/3.0.14'})
        doc = urllib2.urlopen(req)
        info = doc.info()
        try:
            filename = info['content-disposition'].split('=')[1]
            size = int(info['content-length'])
        except:
            logger.error('key error in info')
            continue
        doc.close()
        filename = str(fix_filename(deumlaut(filename))).replace('__', '_')
        match = re_tdt.match(filename)
        if match:
            title = match.group(1)
            date = match.group(2)
            time = match.group(3).replace('-', ':')
        else:
            title = filename
            date = '00-00-00'
            time = '00:00'
        s = Show(None, title, date, time, link, tid, size, Show.NEW)
        logger.info('%s' % s.titleD)
        logger.debug('%s' % s.url)
        s.insert()

########################################
def delete_downloaded(br):
    logger = logging.getLogger()
    
    logger.info('deleting downloaded shows')
    
########################################
def download():
    logger = logging.getLogger()
    
    sql = 'select id, title, date, time, url, telecastid, size, status from shows '
    sql += 'where status = "new" or status = "error" '
    sql += 'order by title, date, time'
    cursor = _database.cursor()
    cursor.execute(sql)
    shows = []
    for row in cursor:
        show = Show(row[0], row[1], row[2], row[3], 
                    row[4], row[5], row[6], row[7])
        shows.append(show)
    cursor.close()
    
    if not shows:
        logger.info('nothing to download')
        return
    
    for show in shows:
        outfile = os.path.join(_config.get('directories', 'storage'),
                               show.filename)
        tmp_outfile = os.path.join(_config.get('directories', 'tmp'),
                                   show.filename)
        
        if os.path.exists(outfile):
            logger.info('%s already exists' % show.titleD)
            show.update_status(Show.DOWNLOADED)
            continue
        
        if os.path.exists(tmp_outfile):
            logger.info('%s tmp file already exists' % show.titleD)
            filesize = os.stat(tmp_outfile)[ST_SIZE]
            if filesize >= show.size:
                logger.info('%s size ok' % show.titleD)
                try:
                    shutil.move(tmp_outfile, outfile)
                except:
                    logger.error('cannot move tmp file')
                continue
            else:
                logger.info('%s size not ok' % show.titleD)
                
        logger.info('downloading %s' % show.titleD)
        show.update_status(Show.DOWNLOADING)
        f = open(tmp_outfile, 'w')
        f.close()
        wget_log = os.path.join(_config.get('directories', 'tmp'), 'wget.log')
        ret = os.system('wget -c %s -O %s -o %s' % (show.url, tmp_outfile, wget_log));
        if os.WIFEXITED(ret):
            if os.WEXITSTATUS(ret) != 0:
                logger.error('wget exited with error')
                show.update_status(Show.ERROR)
                return
        else:
            logger.error('wget died')
            show.update_status(Show.ERROR)
            return
        os.system('touch %s' % tmp_outfile)
        os.chmod(tmp_outfile, 0644)
        try:
            shutil.move(tmp_outfile, outfile)
        except:
            logger.error('cannot move downloaded file')
        show.update_status(Show.DOWNLOADED)
        msg = 'Downloaded %s' % show.titleD
        prowl(msg)
        send_xmpp(msg)

########################################
def prowl(msg):
    try:
        apikey = _config.get('prowl', 'apikey')
    except:
        return
    logger = logging.getLogger()
    p = prowlpy.Prowl(apikey)
    try:
        p.add('save_tv', 'download', msg)
    except Exception:
        logger.error('Prowl failed')
    else:
        logger.info('Prowl sent')

########################################
def send_xmpp(msg):
    try:
        buddy = _config.get('xmpp', 'buddy')
        xuser = _config.get('xmpp', 'username')
        xpass = _config.get('xmpp', 'password')
    except:
        return
    logger = logging.getLogger()
    try:
        jid = xmpp.protocol.JID(xuser)
        cl = xmpp.Client(jid.getDomain(), debug=[])
        cl.connect()
        cl.auth(jid.getNode(), xpass)
        cl.send(xmpp.protocol.Message(buddy, msg))
        cl.disconnect()
    except:
        logger.error('XMPP failed')
    else:
        logger.info('XMPP sent')
    
########################################
def cleanup():
    _database.commit()
    _database.close()

########################################
def exit_handler(signum, stackframe):
    logger.info('killed')
    cleanup()

########################################

_config = None
_database = None
_url_site = 'http://www.save.tv'

def main():
    global _config, _database
    logger = logging.getLogger()
    formatter = logging.Formatter('%(asctime)s: %(levelname)-8s %(message)s')
    hdlr = logging.StreamHandler()
    hdlr.setFormatter(formatter)
    logger.addHandler(hdlr)
    
    _config = SafeConfigParser()
    cfg_file_name = 'save_tv.cfg'
    _config.read([os.path.expanduser('~/.%s' % cfg_file_name), 
                  os.path.join('/', 'etc', cfg_file_name)])
    
    if not _config.has_option('login', 'username') and \
       not _config.has_option('login', 'password'):
        logger.error('config file does not contain login information')
        sys.exit(1)
    if not _config.has_option('directories', 'tmp'):
        logger.error('config file does not define temporary directory')
        sys.exit(1)
    else:
        if not os.path.exists(_config.get('directories', 'tmp')):
            logger.error('temporary directory does not exist')
            sys.exit(1)
    if not _config.has_option('directories', 'storage'):
        logger.error('config file does not define storage directory')
        sys.exit(1)
    else:
        if not os.path.exists(_config.get('directories', 'storage')):
            logger.error('storage directory does not exist')
            sys.exit(1)
    
    opt_verbose = False
    opt_query = False
    opt_download = False

    try:
        opts, args = getopt.getopt(sys.argv[1:], 'hqdv', 
                                   ['help', 'query', 'download', 'verbose'])
        for o, a in opts:
            if o in ('-h', '--help'):
                usage()
                sys.exit()
            if o in ('-q', '--query'):
                opt_query = True
            if o in ('-d', '--download'):
                opt_download = True
            if o in ('-v', '--verbose'):
                opt_verbose = True
    except getopt.GetoptError:
        usage()
    
    log_file = os.path.join(_config.get('directories', 'tmp'), 'save_tv.log')
    hdlr = logging.FileHandler(log_file, 'a')
    hdlr.setFormatter(formatter)
    logger.addHandler(hdlr)
    if opt_verbose:
        logger.setLevel(logging.DEBUG)
    else:
        logger.setLevel(logging.INFO)
    
    if not can_i_run():
        logger.warning('another instance is running')
        sys.exit(1)
    
    _database = connect_to_sqlite()
    
    if not opt_query and not opt_download:
        opt_query = True
        opt_download = True
    
    signal.signal(signal.SIGINT, exit_handler)
    signal.signal(signal.SIGTERM, exit_handler)
    
    if opt_query:
        br = login()
        query(br)
    if opt_download:
        download()
    
    cleanup()
    
    logger.info('done')

########################################
if __name__ == '__main__':
    main()

########################################
# vim:ai:et:ts=4:sw=4
