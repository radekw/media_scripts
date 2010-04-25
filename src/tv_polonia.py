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
import re, logging, sqlite3
import urllib, urllib2
from ConfigParser import SafeConfigParser
from stat import *
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

_shows = {'Klan': 33, 
          'Na Dobre i na Zle': 50, 
          'M jak Milosc': 51, 
          'Barwy Szczescia': 297, 
          'Ojciec Mateusz': 301, 
          'Czas Honoru': 304, 
          'Rajskie Klimaty': 372, 
          'Teleexpress': 61, 
          'Wiadomosci': 61}

########################################
def usage():
    print 'Usage:'
    print '   tv_polonia.py'
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
    def __init__(self, id, title, episode, url, status):
        if id == None:
            self.id = generate_unique_id('%s%s' % (title, episode))
        else:
            self.id = id
        self.title = title
        self.episode = episode
        self.url = url
        self.status = status
        if episode != 0:
            self.titleSE = '%s.%02d' % (title.replace(' ', '.'), episode)
        else:
            self.titleSE = '%s' % title.replace(' ', '.')
        self.filename = '%s.wmv' % self.titleSE
    def insert(self):
        sql = 'insert or ignore into shows '
        sql += '(id, title, episode, url, status) '
        sql += 'values (?, ?, ?, ?, ?)'
        t = (self.id, self.title, self.episode, self.url, self.status)
        _database.execute(sql, t)
        _database.commit()
    def update(self):
        sql = 'update shows '
        sql += 'set title = ?, episode = ?, '
        sql += 'url = ?, status = ? '
        sql += 'where id = ?'
        t = (self.title, self.episode, self.url, self.status, self.id)
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
    f = os.path.join(_config.get('directories', 'storage'), 'tv_polonia.sqlite')
    c = sqlite3.connect(f)
    s = 'CREATE TABLE IF NOT EXISTS shows '
    s += '(id text primary key, title text, '
    s += 'episode integer, url text, status text);'
    c.execute(s)
    return c
    
########################################
def login():
    logger = logging.getLogger()
    br = mechanize.Browser()
    br.addheaders = [('User-agent', _config.get('browser', 'useragent'))]

    logger.info('logging in')
    br.open('https://www.tvpolonia.com/ab/')
    br.select_form(name='form1')
    br['_username'] = _config.get('login', 'username')
    br['_password'] = _config.get('login', 'password')
    res = br.submit()
    
    return br

########################################
def get_base_url(br):
    logger = logging.getLogger()
    
    res = br.open('http://www.tvpolonia.com/player/')
    html = res.read()
    match = re.match(r'.+(mms://.+Bitrate=000).+', html, re.S)
    if match:
        base = match.group(1)
    else:
        logger.error('cannot get base movie URL')
        sys.exit(2)
    # mms://tvpol.wmod.llnwd.net/fc/a295/o2/FILES/?WMContentBitrate=000
    # mms://tvpol.wmod.llnwd.net/fc/a295/o2/FILES/721652898.wmv?WMContentBitrate=750000
    return base

########################################
def get_shows_html(br, path):
    logger = logging.getLogger()
    
    fields = (('cat_offset', '0'), ('movie_offset', '0'), ('path', '%s' % path))
    query_string = '?cat_offset=0&movie_offset=0&path=%s' % path
    post_data = urllib.urlencode(fields)
    res = br.open('http://www.tvpolonia.com/player/categories.php%s' % query_string, 
                  post_data)
    html = res.read()
    return html

########################################
def get_seriale(br, title):
    logger = logging.getLogger()
    try:
        path = _shows[title]
    except KeyError:
        logger.error('Unknown path to show')
        return
    
    base = get_base_url(br)
    html = get_shows_html(br, path)
    
    # onClick="loadmovie('Klan /1855',
    # document.getElementById('moviedesc6').innerHTML,
    # document.getElementById('moviedesc26').innerHTML,'953658175.wmv','17638','1');
    episodes = re.findall(r"loadmovie\('.+?\s/(\d+)'", html)
    files = re.findall(r"innerHTML,'(\d+\.wmv)", html)
    
    for episode, file in zip(episodes, files):
        episode = int(episode)
        url = base.replace('000', '750000')
        url = url.replace('?', '%s?' % file)
        s = Show(None, title, episode, url, Show.NEW)
        logger.info('found %s' % s.titleSE)
        logger.debug('url: %s' % url)
        s.insert()

########################################
def get_wiadomosci(br, title):
    logger = logging.getLogger()
    try:
        path = _shows[title]
    except KeyError:
        logger.error('Unknown path to show')
        return
    
    base = get_base_url(br)
    html = get_shows_html(br, path)
    
    # onClick="loadmovie('Teleexpress 22/4/10',
    # document.getElementById('moviedesc8').innerHTML,
    # document.getElementById('moviedesc28').innerHTML,'474434522.wmv','17820','0');
    all = re.findall(r"loadmovie\('%s.+?\s(\d{1,2})/(\d{1,2})/(\d{2})'.+?innerHTML,'(\d+\.wmv)" % title[:4], html)
    
    for d, m, y, file in all:
        d = int(d)
        m = int(m)
        y = int(y)
        date = '20%02d%02d%02d' % (y, m, d)
        episode = int(date)
        url = base.replace('000', '280000')
        url = url.replace('?', '%s?' % file)
        s = Show(None, title, episode, url, Show.NEW)
        logger.info('found %s' % s.titleSE)
        logger.debug('url: %s' % url)
        s.insert()
    
########################################
def download():
    logger = logging.getLogger()

    sql = 'select id, title, episode, url, status from shows '
    sql += 'where status = "new" or status = "error" '
    sql += 'order by title, episode'
    cursor = _database.cursor()
    cursor.execute(sql)
    shows = []
    for row in cursor:
        show = Show(row[0], row[1], row[2], row[3], row[4])
        shows.append(show)
    cursor.close()
    
    if not shows:
        logger.info('nothing to download')
        return

    for show in shows:
        outdir = os.path.join(_config.get('directories', 'storage'), 
                               show.title)
        if not os.path.exists(outdir):
            os.mkdir(outdir)
        outfile = os.path.join(outdir, show.filename)
        tmp_outfile = os.path.join(_config.get('directories', 'tmp'), 
                                   show.filename)

        if os.path.exists(outfile):
            logger.info('%s already exists' % show.titleSE)
            show.update_status(Show.DOWNLOADED)
            continue
        if os.path.exists(tmp_outfile):
            os.unlink(tmp_outfile)

        logger.info('downloading %s' % show.titleSE)
        show.update_status(Show.DOWNLOADING)
        mplayer_log = os.path.join(_config.get('directories', 'tmp'), 
                                   'mplayer.log')
        ret = os.system('mplayer -dumpstream %s -dumpfile %s >%s 2>&1' % \
                        (show.url, tmp_outfile, mplayer_log));
        if os.WIFEXITED(ret):
            if os.WEXITSTATUS(ret) != 0:
                logger.error('mplayer exited with error')
                show.update_status(Show.ERROR)
                return
        else:
            logger.error('mplayer died')
            show.update_status(Show.ERROR)
            return
        os.system('touch %s' % tmp_outfile)
        os.chmod(tmp_outfile, 0644)
        try:
            shutil.move(tmp_outfile, outfile)
        except:
            logger.error('cannot move downloaded file')
        show.update_status(Show.DOWNLOADED)
        msg = 'Downloaded %s' % show.titleSE
        prowl(msg)
        send_xmpp(msg)

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
        p.add('tv_polonia', 'download', msg)
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

def main():
    global _config, _database
    logger = logging.getLogger()
    formatter = logging.Formatter('%(asctime)s: %(levelname)-8s %(message)s')
    hdlr = logging.StreamHandler()
    hdlr.setFormatter(formatter)
    logger.addHandler(hdlr)
    
    _config = SafeConfigParser()
    cfg_file_name = 'tv_polonia.cfg'
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
    
    log_file = os.path.join(_config.get('directories', 'tmp'), 'tv_polonia.log')
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
        get_seriale(br, 'Klan')
        get_seriale(br, 'M jak Milosc')
        get_seriale(br, 'Na Dobre i na Zle')
        get_seriale(br, 'Barwy Szczescia')
        get_seriale(br, 'Czas Honoru')
        get_seriale(br, 'Ojciec Mateusz')
        get_seriale(br, 'Rajskie Klimaty')
        get_wiadomosci(br, 'Teleexpress')
        get_wiadomosci(br, 'Wiadomosci')
    if opt_download:
        download()
    
    cleanup()
    
    logger.info('done')

########################################
if __name__ == '__main__':
    main()

########################################
# vim:ai:et:ts=4:sw=4
