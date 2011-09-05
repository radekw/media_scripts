#!/usr/bin/python
# Copyright 2010 Radek Wierzbicki
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

"""
This script downloads TV shows from German online VCR recorder - save.tv
You need to have an account and pay for the service in order to user this script.
It's quick, dirty, without much error checking, and without warranty.
If it fails, PECH!
"""

import os, shutil, sys, signal, subprocess, getopt, time, datetime
import re, logging, urllib2, sqlite3, random, stat
from ConfigParser import SafeConfigParser
import mechanize
from hashlib import sha1
from os import urandom

_prowl_available = True
try:
    import prowlpy
except:
    _prowl_available = False
_xmpp_available = True
try:
    import xmpp
except:
    _xmpp_available = False

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
class Shows:
    def __init__(self, tid=None, statuses=[]):
        self.shows = []
        sql = 'select id, title, date, time, extension, url, telecastid, size, status, '
        sql += 'status_update_time from shows '
        if tid or statuses:
            sql += 'where '
            if tid:
                sql += 'telecastid = "%s" ' % tid
            if statuses:
                if tid:
                    sql += 'and ('
                for s in statuses:
                    sql += 'status = "%s" or ' % s
                sql = sql.rstrip('or ')
                if tid:
                    sql += ')'
                sql += ' '
        sql += 'order by status_update_time'
        cursor = _database.cursor()
        cursor.execute(sql)
        for row in cursor:
            show = Show(row[0], row[1], row[2], row[3], row[4], 
                        row[5], row[6], row[7], row[8], row[9])
            self.shows.append(show)
        cursor.close()
    def __len__(self):
        return len(self.shows)
    def __getitem__(self, key):
        return self.shows[key]
    def __iter__(self):
        return iter(self.shows)

########################################
class Show:
    """
    This class stores all information about a show in sqlite database
    """
    NEW = 'new'
    DOWNLOADED = 'downloaded'
    DOWNLOADING = 'downloading'
    ERROR = 'error'
    DELETED = 'deleted'
    def __init__(self, id, title, dt, tm, ext, url, telecastid, size, status, 
                 status_update_time=None):
        if id == None:
            self.id = generate_unique_id('%s%s%s' % (title, dt, tm))
        else:
            self.id = id
        self.title = title.replace(' ', '_')
        self.date = dt
        self.time = tm.replace(':', '')
        self.file_extension = ext
        self.url = url
        self.telecastid = telecastid
        self.size = int(size)
        self.status = status
        self.status_update_time = status_update_time
        self.titleD = self.get_showtitle()
        self.filename = self.get_filename()
    def get_showtitle(self):
        return '%s.%s.%s' % (self.title, self.date, self.time)
    def get_filename(self):
        return '%s.%s' % (self.get_showtitle(), self.file_extension)
    def insert(self):
        sql = 'insert or ignore into shows '
        sql += '(id, title, date, time, extension, url, telecastid, '
        sql += 'size, status, status_update_time) '
        sql += 'values (?, ?, ?, ?, ?, ?, ?, ?, ?, '
        sql += 'datetime("now", "localtime"))'
        t = (self.id, self.title, self.date, self.time, self.file_extension, 
             self.url, self.telecastid, self.size, self.status)
        _database.execute(sql, t)
        _database.commit()
    def update(self):
        sql = 'update shows '
        sql += 'set title = ?, date = ?, time = ?, extension = ?, '
        sql += 'url = ?, telecastid = ?, size = ?, status = ?, '
        sql += 'status_update_time = datetime("now", "localtime") '
        sql += 'where id = ?'
        t = (self.title, self.date, self.time, self.file_extension, self.url, 
             self.telecastid, self.size, self.status, self.id)
        _database.execute(sql, t)
        _database.commit()
    def _string_to_datetime(self, dtstring):
        dt = None
        try:
            dt = datetime.datetime.strptime(dtstring, '%Y-%m-%d %H:%M:%S')
        except:
            dt = None
        return dt
    def get_status_update_datetime(self):
        if self.status_update_time:
            return self._string_to_datetime(self.status_update_time)
        sql = 'select status_update_time where id = ?'
        cursor = _database.cursor()
        cursor.execute(sql, (self.id))
        row = cursor.fetchone()
        if row:
            self.status_update_time = row[0]
        else:
            self.status_update_time = None
        cursor.close()
        if not self.satus_update_time:
            return None
        return self._string_to_datetime(self.status_update_time)
    def get_storage_filename_path(self):
        f = os.path.join(_config.get('directories', 'storage'),
                         self.filename)
        return f
    def get_tmp_filename_path(self):
        f = os.path.join(_config.get('directories', 'tmp'),
                         self.filename)
        return f
    def update_status(self, status):
        self.status = status
        self.update()
    
########################################
def can_i_run():
    """
    Checks if the process is already running
    """
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
    """
    Generates a unique string based on given text or random string
    """
    if l > 40:
        l = 40
    if text is None:
        text = urandom(100)
    return sha1(text).hexdigest()[:l]

########################################
def connect_to_sqlite():
    """
    Connects to sqlite database or create it if does not exists
    """
    f = os.path.join(_config.get('directories', 'storage'), 'save_tv.sqlite')
    c = sqlite3.connect(f)
    s = 'CREATE TABLE IF NOT EXISTS shows '
    s += '(id text primary key, title text, '
    s += 'date text, time text, extension text, url text, telecastid text, '
    s += 'size integer, status text, status_update_time text);'
    c.execute(s)
    return c
    
########################################
def fix_db():
    """
    Checks if sqlite database contains shows with status 'downloading'
    that are not being downloaded anymore (after crash)
    """
    now = datetime.datetime.now()
    shows = Shows(statuses=[Show.DOWNLOADING])
    for show in shows:
        date_diff = now - show.get_status_update_datetime()
        if (date_diff.seconds / 60 / 60) >= 6:
            show.update_status(Show.ERROR)

########################################
def deumlaut(s):
    """
    Replaces umlauts with fake-umlauts
    """
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
    """
    Fixes the file name: remove the user name, replace double underscores
    """
    s = s.replace('_%s' % _config.get('login', 'username'), '')
    s = s.replace('__', '_')
    return s

########################################
def login():
    """
    Opens login page, log in, and return the mechanize browser instance
    """
    logger = logging.getLogger()
    br = mechanize.Browser()
    br.addheaders = [('User-agent', _config.get('browser', 'useragent'))]

    logger.info('logging on')
    try:
        br.open('%s/%s' % (_url_site, '/STV/S/obj/user/usShowLogin.cfm'))
    except:
        logger.error('could not access login page')
        return None
    br.select_form(nr=0)
    br['sUsername'] = _config.get('login', 'username')
    br['sPassword'] = _config.get('login', 'password')
    br.submit()
    
    return br

########################################
def query(br):
    """
    Finds all available shows, gets the info, and inserts into sqlite database
    """
    logger = logging.getLogger()
    random.seed()
    
    if _config.has_option('save_tv', 'use_cut_list'):
        use_cut_list = _config.getboolean('save_tv', 'use_cut_list')
    else:
        use_cut_list = False
    
    # Open 'Mein Videoarchiv' and find all links that contain TelecastID
    # Store all links in shows list
    logger.info('getting show listing')
    shows = []
    try:
        br.open('%s/%s' % (_url_site, '/STV/M/obj/user/usShowVideoArchive.cfm'))
    except:
        logger.error('could not get show listing')
        return
    try:
        links = br.links(url_regex=r'TelecastID')
    except mechanize._mechanize.LinkNotFoundError:
        logger.error('TelecastID links not found')
        return
    for link in links:
        u = '%s/%s' % (_url_site, link.url)
        logger.debug('found %s' % u)
        shows.append(u)
    
    # Get TelecastID out of every link and access web service to obtain
    # download URL
    logger.info('getting show links')
    links = []
    re_tid = re.compile(r'.+TelecastID=(\d+)')
    re_url = re.compile(r".+'(http://.+dl)'.+", re.S)
    for show in shows:
        tid = None
        m = re_tid.match(show)
        if m:
            tid = m.group(1)
        else:
            continue
        if tid:
            show = Shows(tid=tid)
            if show:
                logger.debug('tid %s: already exists in database' % tid)
                continue
            else:
                logger.debug('tid %s: getting link' % tid)
                ts = '%s_%s%s' % (random.randint(1000,9999), 
                                  str(time.time())[:10], 
                                  random.randint(100, 999))
                u = '%s/%s' % (_url_site, 
                             '/STV/M/obj/cRecordOrder/croGetDownloadUrl.cfm')
                u += '?null.GetDownloadUrl'
                u += '&=&ajax=true'
                u += '&c0-id=%s' % ts
                u += '&c0-methodName=GetDownloadUrl'
                u += '&c0-param0=number%%3A%s' % tid
                if use_cut_list:
                    u += '&c0-param1=number%3A1'
                    u += '&c0-param2=boolean%3Atrue'
                else:
                    u += '&c0-param1=number%3A0'
                    u += '&c0-param2=boolean%3Afalse'
                u += '&c0-scriptName=null'
                u += '&callCount=1'
                u += '&clientAuthenticationKey='
                u += '&xml=true'
                logger.debug('tid %s: getter link: %s' % (tid, u))
                try:
                    ret = br.open(u)
                except:
                    logger.error('tid %s: could not get show link' % tid)
                    continue
                html = ret.read()
                m = re_url.match(html)
                if m:
                    link = m.group(1)
                    links.append((tid, link))
                    logger.debug('tid %s: url: %s' % (tid, link))
                else:
                    logger.error('tid %s: no url found' % tid)
    
    # Only connect to download URL just to get file information such as
    # file name, file size, date, time
    # Does not download the file just yet
    logger.info('getting show details')
    re_tdt = re.compile(r'(.+)_{1,2}(\d{2})-(\d{2})-(\d{4})_(\d{2})(\d{2})\.(.+)')
    for tid, link in links:
        req = urllib2.Request(link, headers={'User-agent': 
                                             _config.get('browser', 'useragent')})
        doc = urllib2.urlopen(req)
        info = doc.info()
        try:
            filename = info['content-disposition'].split('=')[1]
            size = int(info['content-length'])
        except:
            logger.error('key error in info')
            continue
        doc.close()
        filename = str(fix_filename(deumlaut(filename)))
        match = re_tdt.match(filename)
        if match:
            title = match.group(1)
            dt = '%s-%s-%s' % (match.group(4), match.group(3), match.group(2))
            tm = '%s%s' % (match.group(5), match.group(6))
            ext = match.group(7)
        else:
            title = filename
            dt = '0000-00-00'
            tm = '0000'
            ext = 'mp4'
        s = Show(None, title, dt, tm, ext, link, tid, size, Show.NEW)
        logger.info('tid %s: title: %s' % (tid, s.titleD))
        logger.debug('tid %s: url: %s' % (tid, s.url))
        s.insert()

########################################
def remove_downloaded(br):
    """
    Removes shows from the website after they are downloaded
    """
    if not _config.has_option('save_tv', 'remove_after_download'):
        return
    elif not _config.getboolean('save_tv', 'remove_after_download'):
        return

    logger = logging.getLogger()
    logger.info('removing downloaded shows from the website')

    # Open 'Mein Videoarchiv'
    # get telecastIDs for downloadable shows
    logger.info('getting show listing')
    try:
        br.open('%s/%s' % (_url_site, '/STV/M/obj/user/usShowVideoArchive.cfm'))
    except:
        logger.error('could not get show listing')
        return
    br.select_form(nr=0)
    tids_site = set()
    try:
        links = br.links(url_regex=r'TelecastID')
    except mechanize._mechanize.LinkNotFoundError:
        logger.error('TelecastID links not found')
        return
    re_tid = re.compile(r'.+TelecastID=(\d+)')
    for link in links:
        m = re_tid.match(link.url)
        if m:
            tids_site.add(m.group(1))

    # intersect website shows with local shows
    # and check the checkboxes
    shows = Shows(statuses=[Show.DOWNLOADED])
    now = datetime.datetime.now()
    for show in shows:
        if show.telecastid in tids_site:
            date_diff = now - show.get_status_update_datetime()
            if date_diff.days >= 1:
                logger.info('removing %s' % show.titleD)
                try:
                    c = br.find_control(name='lTelecastID')
                    c.get(show.telecastid).selected = True
                except:
                    logger.error('failed to select checkbox for %s' % show.telecastid)
                    continue

    # submit form
    br.submit()

########################################
def download():
    logger = logging.getLogger()
    shows = Shows(statuses=[Show.NEW, Show.ERROR])
    
    if not shows:
        logger.info('nothing to download')
        return
    
    for show in shows:
        outfile = show.get_storage_filename_path()
        tmp_outfile = show.get_tmp_filename_path()
        
        if os.path.exists(outfile):
            logger.info('%s already exists' % show.titleD)
            show.update_status(Show.DOWNLOADED)
            continue
        
        if os.path.exists(tmp_outfile):
            logger.info('%s tmp file already exists' % show.titleD)
            filesize = os.stat(tmp_outfile)[stat.ST_SIZE]
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
        user_agent = _config.get('browser', 'useragent')
        wget = 'wget'
        if _config.has_option('directories', 'wget'):
            wget = os.path.join(_config.get('directories', 'wget'), 'wget')
        
        cmd = '%s -c "%s" -O %s -o %s -U "%s"' % (wget, show.url, tmp_outfile, 
                                                  wget_log, user_agent)
        
        if _config.has_option('wget', 'timeout'):
            timeout = _config.getint('wget', 'timeout')
        else:
            timeout = 0
        
        p = subprocess.Popen(cmd, shell=True)
        start_time = time.time()
        while True:
            if p.poll() == None:
                time.sleep(1)
                if timeout:
                    if (time.time() - start_time) > timeout:
                        logger.error('wget timeout - killing')
                        p.kill()
                        break
            else:
                logger.debug('wget exited')
                break
        
        ret = p.returncode
        if ret > 0:
            logger.error('wget exited with error')
            show.update_status(Show.ERROR)
            return
        elif ret < 0:
            logger.error('wget died')
            show.update_status(Show.ERROR)
            return
        
        os.system('touch %s' % tmp_outfile)
        
        os.chmod(tmp_outfile, 0644)
        
        try:
            shutil.move(tmp_outfile, outfile)
        except:
            logger.error('cannot move downloaded file')
            show.update_status(Show.ERROR)
            return
        
        show.update_status(Show.DOWNLOADED)
        msg = 'Downloaded %s' % show.titleD
        prowl(msg)
        send_xmpp(msg)

########################################
def delete_old_shows():
    """
    Deletes shows from disk after number of days configured in cfg file
    """
    logger = logging.getLogger()
    retain_days = _config.getint('directories', 'retain_days')
    if retain_days == 0:
        return

    logger.info('deleting downloaded shows')
    now = datetime.datetime.now()
    shows = Shows(statuses=[Show.DOWNLOADED])
    for show in shows:
        date_diff = now - show.get_status_update_datetime()
        if date_diff.days > retain_days:
            outfile = show.get_storage_filename_path()
            logger.info('deleting %s' % show.titleD)
            if os.path.exists(outfile):
                try:
                    os.unlink(outfile)
                except:
                    logger.error('could not delete %s' % show.titleD)
                    continue
            else:
                logger.debug('%s previously deleted' % show.titleD)
            show.update_status(Show.DELETED)
    
########################################
def prowl(msg):
    if not _prowl_available:
        return
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
    if not _xmpp_available:
        return
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
    logger = logging.getLogger()
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
    fix_db()
    
    if not opt_query and not opt_download:
        opt_query = True
        opt_download = True
    
    signal.signal(signal.SIGINT, exit_handler)
    signal.signal(signal.SIGTERM, exit_handler)
    
    if opt_query:
        br = login()
        if br:
            query(br)
            remove_downloaded(br)
    if opt_download:
        download()
        delete_old_shows()
    
    cleanup()
    
    logger.info('done')

########################################
if __name__ == '__main__':
    main()

########################################
# vim:ai:et:ts=4:sw=4
