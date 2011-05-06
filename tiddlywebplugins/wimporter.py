"""
A plugin for importing a TiddlyWiki into a TiddlyWeb,
via the web, either by uploading a file or providing
a URL. This differs from other tools in that it provides
a selection system.

This is intentionally a UI driven thing, rather than API
driven.

Two templates are used 'chooser.html' and 'wimport.html'.
These may be overridden locally. See tiddlywebplugins.templates.

To use add 'tiddlywebplugins.wimporter' to an instance's
tiddywebconfig.py:

    config = {
        'system_plugins': ['tiddlywebplugins.wimporter'],
    }
"""

import cgi
import operator
import urllib2

from uuid import uuid4 as uuid

from tiddlywebplugins.utils import entitle, do_html
from tiddlywebplugins.templates import get_template
from tiddlywebplugins.twimport import (import_one,
        wiki_string_to_tiddlers, get_url_handle)

from tiddlyweb.control import filter_tiddlers
from tiddlyweb.model.bag import Bag
from tiddlyweb.model.policy import ForbiddenError, UserRequiredError
from tiddlyweb.model.tiddler import Tiddler
from tiddlyweb.store import NoBagError
from tiddlyweb.web.util import bag_url
from tiddlyweb.web.http import HTTP302


def init(config):
    if 'selector' in config:
        config['selector'].add('/import', GET=interface, POST=wimport)


@entitle('Import Tiddlers')
@do_html()
def interface(environ, start_response):
    return _send_wimport(environ, start_response)


@entitle('Import Tiddlers')
@do_html()
def wimport(environ, start_response):
    form = cgi.FieldStorage(fp=environ['wsgi.input'], environ=environ)
    if 'url' in form or 'file' in form:
        tmp_bag = _make_bag(environ)
        try:
            if form['url'].value:
                _process_url(environ, form['url'].value, tmp_bag)
            if form['file'].filename:
                _process_file(environ, form['file'].file, tmp_bag)
            fixed_bag = environ['tiddlyweb.query'].get('bag', [None])[0]
            return _show_chooser(environ, tmp_bag, fixed_bag)
        except AttributeError, exc:  # content was not right
            return _send_wimport(environ, start_response,
                    'that was not a wiki %s' % exc)
        except ValueError, exc:  # file or url was not right
            return _send_wimport(environ, start_response,
                    'could not read that %s' % exc)
        except (OSError, urllib2.URLError), exc:
            return _send_wimport(environ, start_response,
                    'trouble reading: %s' % exc)

    elif 'target_bag' in form:
        return _process_choices(environ, start_response, form)
    else:
        return _send_wimport(environ, start_response, 'missing field info')


def _process_choices(environ, start_response, form):
    store = environ['tiddlyweb.store']
    user = environ['tiddlyweb.usersign']

    tmp_bag = form['tmp_bag'].value.decode('utf-8', 'ignore')
    bag = form['target_bag'].value.decode('utf-8', 'ignore')
    if bag:
        bag = Bag(bag)
        try:
            bag.skinny = True
            bag = store.get(bag)
        except NoBagError:
            return _send_wimport(environ, start_response,
                    'chosen bag does not exist')
    else:
        bag = form['new_bag'].value.decode('utf-8', 'ignore')
        bag = _make_bag(environ, bag)

    try:
        bag.policy.allows(user, 'write')
    except (ForbiddenError, UserRequiredError):
        return _send_wimport(environ, start_response,
                'you may not write to that bag')

    tiddler_titles = form.getlist('tiddler')
    if tiddler_titles:
      for title in tiddler_titles:
        tiddler = Tiddler(title.decode('utf-8', 'ignore'), tmp_bag)
        tiddler = store.get(tiddler)
        tiddler.bag = bag.name
        store.put(tiddler)
    else:
      for tiddler in store.list_bag_tiddlers(Bag(tmp_bag)):
        tiddler.bag = bag.name
        store.put(tiddler)
    store.delete(Bag(tmp_bag))
    bagurl = bag_url(environ, bag) + '/tiddlers'
    raise HTTP302(bagurl)
    

def _show_chooser(environ, tmp_bag, fixed_bag):
    # refresh the bag object
    store = environ['tiddlyweb.store']
    tmp_bag.skinny = True
    tmp_bag = store.get(tmp_bag)
    tiddlers = filter_tiddlers(store.list_bag_tiddlers(tmp_bag), 'sort=title')
    template = get_template(environ, 'chooser.html')
    return template.generate(tiddlers=tiddlers,
            tmp_bag=tmp_bag.name,
            fixed_bag=fixed_bag,
            bags=_get_bags(environ))


def _process_url(environ, url, bag):
    try:
        import_one(bag.name, url, environ['tiddlyweb.store'])
    except ValueError:
        # automatic detection did not work, fail over to wiki
        # XXX: later add sniffing
        url, handle = get_url_handle(url)
        _process_file(environ, handle, bag)


def _process_file(environ, filehandle, bag):
    wikitext = filehandle.read().decode('utf-8', 'replace')
    filehandle.close()
    tiddlers = wiki_string_to_tiddlers(wikitext)
    store = environ['tiddlyweb.store']
    for tiddler in tiddlers:
        tiddler.bag = bag.name
        store.put(tiddler)


def _make_bag(environ, bag_name=None):
    bag_name = bag_name or "import-tmp-%s" % str(uuid())
    store = environ['tiddlyweb.store']
    bag = Bag(bag_name)
    _set_restricted_policy(environ, bag)
    store.put(bag)
    return bag


def _set_restricted_policy(environ, bag):
    """
    Set this bag to only be visible and usable by
    the current user, if the current user is not
    guest.
    """
    username = environ['tiddlyweb.usersign']['name']
    if username == 'GUEST':
        return
    bag.policy.owner = username
    # accept does not matter here
    for constraint in ['read', 'write', 'create', 'delete', 'manage']:
        setattr(bag.policy, constraint, [username])
    return


def _send_wimport(environ, start_response, message=''):
    query = environ["tiddlyweb.query"]
    bag = query.get("bag", [None])[0]
    template = get_template(environ, 'wimport.html')
    return template.generate(bag=bag, message=message)


def _get_bags(environ):
    store = environ['tiddlyweb.store']
    user = environ['tiddlyweb.usersign']
    bags = store.list_bags()
    kept_bags = []
    for bag in bags:
        bag = store.get(bag)
        try:
            bag.policy.allows(user, 'write')
            if not bag.name.startswith('import-tmp'):
                kept_bags.append(bag)
            continue
        except (ForbiddenError, UserRequiredError):
            pass

    return sorted(kept_bags, key=operator.attrgetter('name'))
