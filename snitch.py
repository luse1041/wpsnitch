# This software is in the public domain. Where that dedication is not
# recognized, you are granted a perpetual, irrevocable license to copy,
# distribute, and modify this file as you see fit.

import datetime
import json
import logging
import pickle
import urllib2
from urlparse import urlparse
from xml.dom import minidom

from google.appengine.ext import db

class InvalidUrlException(Exception):
    pass

class InternalErrorException(Exception):
    pass

class AppCache(db.Model):
    sku_id = db.StringProperty(required=True)
    data = db.TextProperty(required=True)
    updated = db.DateTimeProperty(auto_now=True)

class Snitch(object):
    def __init__(self, url):
        self.url = url
        self.sku_id = None
        self.guid = None
        self.data = {}

    def get_sku_id(self):
        parsed_url = urlparse(self.url)

        if parsed_url.netloc != 'www.microsoft.com':
            raise InvalidUrlException

        parts = parsed_url.path.split('/')

        try:
            self.sku_id = parts[5].lower()[:12]
        except IndexError:
            raise InvalidUrlException

    def get_cache(self):
        q = db.Query(AppCache)
        q.filter('sku_id =', self.sku_id)
        q.filter('updated >', datetime.datetime.now() \
                              - datetime.timedelta(days=1))
        entry = q.get()

        if entry:
            self.data = pickle.loads(entry.data.encode('windows-1252'))

    def set_cache(self):
        data = pickle.dumps(self.data).decode('windows-1252')

        q = db.Query(AppCache)
        q.filter('sku_id =', self.sku_id)
        entry = q.get()

        if entry:
            logging.info('Cache refresh: %s' % self.sku_id)
            entry.data = data
        else:
            entry = AppCache(sku_id=self.sku_id, data=data)

        entry.put()

    def get(self):
        # Get the SKU id from the url
        self.get_sku_id()

        # check the cache and return already if it's populated
        self.get_cache()

        if self.data:
            return

        logging.info('Cache miss: %s' % self.sku_id)

        # First, we need to convert the SKU id (used in Windows 10)
        # to a GUID (used in Windows 8.1, the only API that still shows
        # the last update timestamp). For that, we need to use the Windows 10
        # API.
        request_url = 'https://storeedgefd.dsx.mp.microsoft.com/pages/pdp?' \
            'productId={sku_id}&idType=ProductId&skuId=&catalogIds=' \
            '&catalogTicketKeys=&appversion=2015.25.24.0&itemType=Apps' \
            '&market=US&locale=en-US&deviceType=&deviceFamily=windows.desktop' \
            '&catalogLocales=en-US&musicMarket=US&screenSize=L' \
            '&hardware=dx9%2Cdxa%2Ckbd%2Cm30%2Cm75%2CmA0%2Cmse%2CmT0' \
            '&packageHardware=dx9%2Cdxa%2Cm30%2Cm75%2CmA0%2CmT0' \
            '&deviceFamilyVersion=2814750460870760&architecture=x64' \
            '&deviceFamilyFilter=windows.desktop&oemId=Public&scmId=Public' \
            '&moId=Public'.format(sku_id=self.sku_id)
        headers = {'MS-Contract-Version': '4'}

        try:
            request = urllib2.Request(request_url, headers=headers)
            response = urllib2.urlopen(request).read()
        except urllib2.URLError, e:
            raise InternalErrorException('Error 1')

        try:
            response_dict = json.loads(response)
        except ValueError, e:
            raise InternalErrorException('Error 2')

        # we'll parse the last payload
        try:
            payload = response_dict[-1]['Payload']
        except KeyError, e:
            raise InternalErrorException('Error 3')

        try:
            package_names = payload['PackageFamilyNames']
            self.guid = package_names[-1].split('_')[0]
        except KeyError, e:
            raise InternalErrorException('Error 4. Probably an invalid URL')

        # now that we have the guid, query the 8.1 API
        request_url = 'http://marketplaceedgeservice.windowsphone.com/v9/' \
            'catalog/apps/{guid}?os=8.10.12393.0&cc=us&lang=en-us&moId=' \
            .format(guid=self.guid)

        try:
            request = urllib2.Request(request_url, headers=headers)
            response = urllib2.urlopen(request).read()
        except urllib2.URLError, e:
            # if an app is not available for the WP 8.1 store, bad luck
            raise InternalErrorException('This is a PC-only, UWP-only or ' \
                                         'Windows 10-only app, which are not ' \
                                         'supported.')

        try:
            # remove the stupid BOM
            while not response.startswith('<') and len(response) > 0:
                response = response[1:]
            xml = minidom.parseString(response)
        except xml.parsers.expat.ExpatError, e:
            raise InternalErrorException('Error 6')

        # and now parse it
        def xml_get(element):
            value = xml.getElementsByTagName(element)[0].firstChild.nodeValue
            if not value:
                raise InternalErrorException('Error 7')
            return value

        def fix_date(date):
            if date == '1601-01-01T00:00:00.000000Z':
                return 'Never'

            return date[:19].replace('T', ' ')

        try:
            self.data['name'] = xml_get('a:title')
            self.data['version'] = xml_get('version')
            self.data['last_updated'] = fix_date(xml_get('skuLastUpdated'))
            self.data['release_date'] = fix_date(xml_get('releaseDate'))
            self.data['download_link'] = xml_get('url')
            self.data['package_format'] = xml_get('packageFormat')
            tmp = xml_get('packageSize')
            self.data['package_size'] = '%.2f' % (float(tmp) / (1024 * 1024),)
        except TypeError, e:
            raise InternalErrorException('Error 8')

        self.set_cache()
