#!/usr/bin/python
# -*- coding: utf-8 -*-

# Copyright 2010-2012 CDPedistas (see AUTHORS.txt)
#
# This program is free software: you can redistribute it and/or modify it
# under the terms of the GNU General Public License version 3, as published
# by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranties of
# MERCHANTABILITY, SATISFACTORY QUALITY, or FITNESS FOR A PARTICULAR
# PURPOSE.  See the GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program.  If not, see <http://www.gnu.org/licenses/>.
#
# For further info, check  http://code.google.com/p/cdpedia/

"""Download the whole wikipedia."""

from __future__ import with_statement

import re
import os
import sys
import gzip
import time
import urllib
import datetime
import tempfile
import StringIO
import re
from functools import partial

import eventlet
from eventlet.green import urllib2

import to3dirs

# Artículos que no se descargaron por alguna razón.
ARTICLES_TO_RETRY = "probar_de_nuevo.txt"

WIKI = 'http://es.wikipedia.org/'

UA = 'Mozilla/5.0 (X11; U; Linux i686; en-US; rv:1.9.2.10) Gecko/20100915 ' \
     'Ubuntu/10.04 (lucid) Firefox/3.6.10'

req = partial(urllib2.Request, data = None,
              headers = {'User-Agent': UA, 'Accept-encoding':'gzip'})

OK, NO_EXISTE, HAY_QUE_PROBAR_DE_NUEVO = range(3)



class URLAlizer(object):
    def __init__(self, listado_nombres, dest_dir):
        self.dest_dir = dest_dir
        self.temp_dir = dest_dir + ".tmp"
        if not os.path.exists(self.temp_dir):
            os.makedirs(self.temp_dir)
        self.fh = open(listado_nombres, 'r')

        # saltea la primera linea
        prim_linea = self.fh.readline()
        assert prim_linea.strip() == "page_title"

    def next(self):
        while True:
            line = self.fh.readline()
            if line == "":
                raise StopIteration
            basename = line.decode("utf-8").strip()
            path = os.path.join(self.dest_dir, to3dirs.to_path(basename))
            disk_name = os.path.join(path, to3dirs.to_filename(basename))
            if not os.path.exists(disk_name.encode('utf-8')):
                if not os.path.exists(path.encode('utf-8')):
                    os.makedirs(path.encode('utf-8'))

                temp_file = tempfile.NamedTemporaryFile(suffix=".html",
                              prefix="scrap-", dir=self.temp_dir, delete=False)
                quoted_url = urllib.quote(basename.encode('utf-8'))
                # Skip wikipedia automatic redirect
                url = u"%sw/index.php?title=%s&redirect=no" % (WIKI, quoted_url)
                return url, temp_file, disk_name, self, basename

    def __iter__(self):
        return self


def fetch_html(url):
    """
    Note: this follows redirects
    """
    #print 'fetching:', repr(url)
    response = urllib2.urlopen(req(url))
    compressedstream = StringIO.StringIO(response.read())
    gzipper = gzip.GzipFile(fileobj=compressedstream)
    html = gzipper.read()
    return html


compose_funcs = lambda f,g: (lambda x: f(g(x)))

class WikipediaWebBase:
    @staticmethod
    def URL_ENC(preurl):
        return preurl.replace(' ', '_')

    @staticmethod
    def QUOTE(*args):
        conv = lambda s: s if type(s)==type('') else s.encode('utf-8')
        return tuple(map(compose_funcs(urllib.quote, conv), args))


class WikipediaUser(WikipediaWebBase):
    """
    Given a user-id or a line of a wikipedia-page's history, create the 
    asociated user.

    It will be able to answer (by querying the web) if it's a bot, a registered 
    user or none of them (ie: an anonymouse user).
    """
    USUARIO_RE = re.compile('title="Usuario\:([^"]*)"')
    CONTRIB_RE = re.compile('title="Especial\:([^"]*)"')
    NO_USER_PAGE_YET = ' (aún no redactado)'
    BotDict = {}

    @classmethod
    def FromHistory(cls, history_line):
        _USER_REs = [(cls.USUARIO_RE, True), (cls.CONTRIB_RE, False) ]
        user = None #user = ('not','found')
        first = None
        for user_re, registered in _USER_REs:
            m = user_re.search(history_line)
            if m:
                userid = m.groups()[0]
                pos = history_line.find(userid)
                if first is None or pos<first[0]:
                    # we need to track which occurres first
                    first = pos, userid, registered
        if first is not None:
            _, userid, registered = first
            return WikipediaUser(userid, registered)

    def __init__(self, userid, registered):
        self.has_page = not userid.endswith(self.NO_USER_PAGE_YET)

        if self.has_page:
            self.userid = userid
        else:
            self.userid = userid[:-len(self.NO_USER_PAGE_YET)]

        self.registered = registered # False=anonymous
        if not registered or not self.has_page:
            # Assume that if the user have no page defined,
            # it shouldn't be not a robot..
            self.BotDict[self.userid]=False
    
    def __str__(self):
        return '<id:%s registered:%s bot:%s>'%(self.userid, self.registered, self.is_bot())

    @property
    def user_url(self):
        if not self.has_page:
            return None
        preurl = 'http://es.wikipedia.org/wiki/Usuario:%s'
        return self.URL_ENC( preurl % self.QUOTE(self.userid) )

    @staticmethod
    def url_to_relative(url):
        globaldomain = 'wikipedia.org'
        assert globaldomain in url
        return url.split(globaldomain, 1)[1]

    @property
    def _is_bot(self):
        return self.BotDict.get(self.userid)

    def is_bot(self):
        if self._is_bot is None:
            self._check_botness()
        return self._is_bot

    def is_anonymous(self):
        return not self.registered

    def _check_botness(self):
        user_info_page = fetch_html(self.bot_check_url)
        self._is_bot = self.url_to_relative(self.user_url) in user_info_page
        self.BotDict[self.userid] = self._is_bot
        return self._is_bot

    @property
    def bot_check_url(self):
        preurl = 'http://es.wikipedia.org/w/index.php?title=Especial:ListaUsuarios&group=bot&limit=1&username=%s'
        return self.URL_ENC( preurl % self.QUOTE(self.userid) )


class WikipediaPage(WikipediaWebBase):
    """
    Represents a wikipedia page. 
    
    It should know how to retrive the asociated history page and any revision.
    """
    #these should be setup by a localized subclass
    HISTORY_BASE = None
    HISTORY_CLASS = None 
    REVISION_URL = None

    def __init__(self, datos):
        """
        datos : tuple with:  url, temp_file, disk_name, uralizer, basename
        """
        self.url, temp_file, disk_name, uralizer, self.basename = datos
        self._history = None

    def __str__(self):
        return '<wp: %s>'%self.basename

    @property
    def history_url(self):
        return self.URL_ENC( self.HISTORY_BASE % self.QUOTE(self.basename) )

    def get_revision_url(self, revision=None):
        """ 
        Return the revision url when revision is provided, elsewhere the basic 
        url for the page 
        """
        if revision is None:
            return self.url 
        return self.URL_ENC(self.REVISION_URL % self.QUOTE(self.basename, revision))

    def get_history(self):
        if self._history is None:
            self._history = fetch_html(self.history_url)
        return self._history

    def _iter_history(self):
        for line in self.get_history().split('<li>')[1:]:
            yield line.split('</li>',1)[0]

    def iter_history(self):
        for line in self._iter_history():
            yield self.HISTORY_CLASS (self, line)

    def search_valid_version(self, acceptance_days=7, _show_debug_info=False):
        """ 
        This function will search for a "good-enough" version of the page wanted.
        
        Where good-enough means:
         * Page version is commited by a registered user (being it human or bot).
         * Page version is commited by an unregistered user and stayed alive longer
            than 'acceptance_days'.

        Check issue #124 at: http://code.google.com/p/cdpedia/issues/detail?id=124
        """
        self.acceptance_delta = datetime.timedelta(acceptance_days)
        prev_date = datetime.datetime.now()
        
        for idx, hi in enumerate(self.iter_history()):
            if self.validate_revision(hi, prev_date):
                break #return hi.page_rev_id
            prev_date = hi.date
        else:
            raise Exception('No version for: %s'%str(self))
        
        if idx!=0 and 1:
            print 'warning: possible vandalism:', str(self), idx
        return self.get_revision_url(hi.page_rev_id)

    def validate_revision(self, hist_item, prev_date):
        # if the user is registered, it's enough for us! (even if it's a bot)
        if hist_item.user.registered:
            return True
        #if it's not registered, check for how long this version lasted
        if hist_item.date + self.acceptance_delta < prev_date:
            return True
        return False


class WikipediaPageHistoryItem:
    def __init__(self, page, line):
        self.page = page
        self.user = WikipediaUser.FromHistory(line)
        self.page_rev_id = self._get_page_version_id(line) 
        self.date = self._get_page_version_date(line)

    @classmethod
    def _get_page_version_id(cls, line):
        """
        Returns the version id if found, None if not
        """
        m = cls.PAGE_VERSION_ID.match(line)
        if m:
            id_url = m.groups()[0]
            m = cls.ID_RE.match(id_url)
            if m:
                return m.groups()[0]

    @classmethod
    def _get_page_version_date(cls, line):
        """
        Returns the version date if found, None if not
        """
        m = cls.PAGE_VERSION_DATE.match(line)
        if m:
            hour, minute, day, month, year = m.groups()
            month = cls.MONTH_NAMES.index(month)+1
            tdate = tuple([int(x) for x in (year, month, day, hour, minute)])
            return datetime.datetime(*tdate)

    def __str__(self):
        return '<rev: by %s id %r %r>'%(self.user, self.page_rev_id, self.date)
   

class WikipediaPageHistoryItemES (WikipediaPageHistoryItem):
    PAGE_VERSION_ID = re.compile('.*<a href="([^"]*)" title="[^"]*">act</a>.*')
    PAGE_VERSION_DATE = re.compile(
                       '.*>([0-9]*):([0-9]*) ([0-9]*) ([a-z]*) ([0-9]*)</a>.*')
    COMMENT_RE = re.compile('<span class="comment">([^]]*)')
    ID_RE = re.compile(".*oldid=([0-9]*).*")
    MONTH_NAMES = ['ene', 'feb', 'mar', 'abr', 'may', 'jun', 'jul', 'ago', 
                    'sep', 'oct', 'nov', 'dic']


class WikipediaPageES(WikipediaPage):
    REVISION_URL = 'http://es.wikipedia.org/w/index.php?title=%s&oldid=%s'
    HISTORY_BASE = 'http://es.wikipedia.org/w/index.php?title=%s&action=history'
    HISTORY_CLASS =  WikipediaPageHistoryItemES 




regex = '(<h1 id="firstHeading" class="firstHeading">.+</h1>)(.+)\s*<!-- /catlinks -->'
capturar = re.compile(regex, re.MULTILINE|re.DOTALL).search
no_ocultas = re.compile('<div id="mw-hidden-catlinks".*?</div>',
                                                re.MULTILINE|re.DOTALL)
no_pp_report = re.compile("<!--\s*?NewPP limit report.*?-->",
                                                re.MULTILINE|re.DOTALL)
def extract_content(html):
    encontrado = capturar(html)
    if not encontrado:
        # Si estamos acá, el html tiene un formato diferente.
        # Por el momento queremos que se sepa.
        raise ValueError, "El archivo %s posee un formato desconocido" % wikiarchivo.url
    newhtml = "\n".join(encontrado.groups())

    # algunas limpiezas más
    newhtml = no_ocultas.sub("", newhtml)
    newhtml = no_pp_report.sub("", newhtml)

    return newhtml

def fetch(datos):
    page = WikipediaPageES(datos)
    version_url = page.search_valid_version()
    url, temp_file, disk_name, uralizer, basename = datos
    url = version_url

    try:
        html = fetch_html(url)
    except urllib2.HTTPError, e:
        if e.code == 404:
            return NO_EXISTE, basename
        if e.code == 403:
            return HAY_QUE_PROBAR_DE_NUEVO, basename
        print>>sys.stderr, "%s : %s" % (url, e.code)
        return HAY_QUE_PROBAR_DE_NUEVO, basename
    except Exception, e:
        print>>sys.stderr, "%s : %s" % (url, e)
        return HAY_QUE_PROBAR_DE_NUEVO, basename

    # ok, downloaded the html, let's check that it complies with some rules
    if "</html>" not in html:
        # we surely didn't download it all
        return HAY_QUE_PROBAR_DE_NUEVO, basename
    try:
        html.decode("utf8")
    except UnicodeDecodeError:
        return HAY_QUE_PROBAR_DE_NUEVO, basename

    try:
        html = extract_content(html)
    except ValueError:
        return HAY_QUE_PROBAR_DE_NUEVO, basename

    with temp_file as fh:
        fh.write(html)
    try:
        os.rename(temp_file.name, disk_name.encode("utf-8"))
    except OSError as e:
        print "error creating this file:", disk_name.encode("utf-8")
        return HAY_QUE_PROBAR_DE_NUEVO, basename
    return OK, basename

def main(nombres, dest_dir, pool_size=20):
    pool = eventlet.GreenPool(size=int(pool_size))
    urls = URLAlizer(nombres, dest_dir)

    probar_de_nuevo_file = open(ARTICLES_TO_RETRY, "a", buffering=0)
    total, bien, mal, hay_que_probar_de_nuevo = 0, 0, 0, 0
    tiempo_inicial = time.time()
    try:
        for status, basename in pool.imap(fetch, urls):
            total += 1
            if status == OK:
                bien += 1
            elif status == NO_EXISTE:
                mal += 1
            elif status == HAY_QUE_PROBAR_DE_NUEVO:
                mal += 1
                probar_de_nuevo_file.write(basename.encode("utf-8")+"\n")
                probar_de_nuevo_file.flush()

            velocidad = total/(time.time()-tiempo_inicial)
            sys.stdout.write("\r TOTAL=%d \t BIEN=%d \t MAL=%d \t velocidad = %.2f art/s" %
                             (total, bien, mal, velocidad))
            sys.stdout.flush()

    except (KeyboardInterrupt, SystemExit):
        print "\nStoping, plase wait."

USAGE = """
Usar: scraper.py <NOMBRES_ARTICULOS> <DEST_DIR> [CONCURRENT]"
  Descarga la wikipedia escrapeándola.

  NOMBRES_ARTICULOS es un listado de nombres de artículos. Debe ser descargado
  y descomprimido de:
  http://download.wikipedia.org/eswiki/latest/eswiki-latest-all-titles-in-ns0.gz

  DEST_DIR es el directorio de destino, donde se guardan los artículos. Puede
  ocupar unos 40GB o más.

  CONCURRENT es la cantidad de corrutinas que realizan la descarga. Se puede
  tunear para incrementar velocidad de artículos por segundo. Depende mayormente
  de la conexión: latencia, ancho de banda, etc. El default es 20.

  Los nombres de los artículos que no pudieron descargarse correctamente se
  guardan en probar_de_nuevo.txt.

"""

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print USAGE
        sys.exit(1)

    main(*sys.argv[1:])
