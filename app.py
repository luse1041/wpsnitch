# This software is in the public domain. Where that dedication is not
# recognized, you are granted a perpetual, irrevocable license to copy,
# distribute, and modify this file as you see fit.

import jinja2
import json
import webapp2
import os

from snitch import Snitch, InvalidUrlException, InternalErrorException

template_dir = os.path.join(os.path.dirname(__file__), 'templates')
jinja_env = jinja2.Environment(loader=jinja2.FileSystemLoader(template_dir),
                               autoescape=True)

class Handler(webapp2.RequestHandler):
    def write(self, *a, **kw):
        self.response.out.write(*a, **kw)

    def render_str(self, template, **params):
        t = jinja_env.get_template(template)
        return t.render(params)

    def render(self, template, **kw):
        self.write(self.render_str(template, **kw))

class MainPage(Handler):
    def get(self):
        self.render('index.html')

class FetchPage(Handler):
    def json_write(self, text):
        self.response.write(json.dumps({'output': text}))

    def post(self):
        self.response.headers['Content-Type'] = 'application/json'

        url = self.request.get('url')
        snitch = Snitch(url)

        try:
            snitch.get()
            output = self.render_str('fetch.html', data=snitch.data)
            self.json_write(output)
        except InvalidUrlException:
            self.json_write('This is not a valid URL it seems.')
        except InternalErrorException, e:
            self.json_write(e.message)

app = webapp2.WSGIApplication([
    ('/', MainPage),
    ('/fetch', FetchPage),
], debug=False)
