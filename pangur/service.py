from urllib import quote_plus
from datetime import datetime, timedelta
from werkzeug import Request, Response
from werkzeug.exceptions import NotFound
from jinja2 import Environment, FileSystemLoader, ext

from pangur import session, utils, database, plugin, staticfiles
from pangur.exceptions import HTTPException, RedirectException
from pangur.utils import FormsRegistry

#grevious bodily hack, because jinja cant access __names  c_c
import wtforms
wtforms.Form.getFormName = lambda self: self.__class__.__name__

templates = None
db_conn = None
conf = None
static = None
pastDate = datetime(2000,1,1)

#decorator to register before render funcs
@utils.registryDecorator
def beforeTemplateRender(registry, func):
    registry.append(func)


def init(config, _package_=None):
    """Init application: pass config and __package__ to load."""
    global templates, db_conn, conf, static
    conf = config
    db_conn = database.DB(conf) #Establish a connection to the db
    #load all modules in the app package.
    if _package_:
        plugin.importPackage(_package_)
    #make jinja template environment.
    template_paths = conf.paths.templates + utils.templatePaths
    templates = Environment(loader=FileSystemLoader(template_paths),
                            autoescape=True,
                            extensions=['pangur.service.JinjaUid'])
    #print '\n'.join(templates.list_templates(filter_func=lambda n: not n.startswith('.')))
    #map static files.
    static = staticfiles.FileServer(utils.staticPaths)
    return application


def prepareToRender(request):
    request.forms = FormsRegistry(request)
    vars = {'request' : request, 'forms' : request.forms }
    for func in beforeTemplateRender.values():
        func(request, vars)
    return vars

@Request.application
def application(request):
    adapter = utils.url_map.bind_to_environ(request.environ)
    try:
        endpoint, values = adapter.match()
        templateName = values['template']
        request.matches = values
        func = values['func']
        permission = values['permission']
        request.response = Response(
            mimetype=values.get('mimetype') or 'text/html')
        if values.get('nocache'):
            request.response.expires = pastDate
            request.response.cache_control.no_cache = True
            request.response.cache_control.no_store = True
            request.response.headers['Pragma'] = 'no-cache'
        elif values.get('expires'):
            request.response.expires = datetime.utcnow() + \
                timedelta(seconds=values.get('expires'))
        request.conf = conf
        request.db = db_conn
        request.txn = request.db.begin()
        request.session = session.Session(request)
        request.relative = lambda p="", **kw: utils.relative(request, p, **kw)
        templateVars = prepareToRender(request)
        #handle authRequired resource when user is not logged in
        if (values['authRequired'] or permission) and not request.session.authenticated:
            path = quote_plus(request.path.encode('utf-8'))
            raise RedirectException(
                conf.URLS.login.format(fromPath=path))
        if not permission or request.session.user.hasPermission(permission):
            #we are allowed to be here so..
            if templateName and callable(func):
                #call function and merge returned dict with templateVars
                #then render the template
                templateVars.update(func(request) or {})
                template = templates.get_template(templateName)
                request.response.data = template.render(**templateVars)
            elif templateName:
                #just render the template vanilla, yum!
                template = templates.get_template(templateName)
                request.response.data = template.render(**templateVars)
            elif callable(func):
                #just call the function to handle the request
                data = func(request)
                if data is not None:
                    request.response.data = data
        elif request.session.authenticated:
            #we are logged in but should not be here, oops!
            template = templates.get_template(conf.URLS.no_permissions)
            request.response.data = template.render(**templateVars)
        else:
            #you are not logged in, eek, return to home, do not pass go
            #do not collect $200-
            raise RedirectException(
                conf.URLS.login.format(fromPath=request.path))

    except NotFound, e:
        # let the static file server respond or 404.
        return static
    except HTTPException, e:
        if hasattr(e, 'handler'):
            try:
                e.handler(request)
            except Exception, e:
                print "Exception Handling Error", e
        else:
            return e
    request.txn.commit()
    return request.response


# https://github.com/mitsuhiko/werkzeug/issues/122
# gunicorn sometimes passes SERVER_PORT as an int instead of a str.
def fix_server_port(app):
    def new_app(environ, start_response):
        environ['SERVER_PORT'] = str(environ['SERVER_PORT'])
        return app(environ, start_response)
    return new_app
application = fix_server_port(application)


class JinjaUid(ext.Extension):
    """Adds a uid() global function that returns a unique id."""
    def __init__(self, environment):
        ext.Extension.__init__(self, environment)
        environment.globals['uid'] = self.nextId
        self.id = 0
    def nextId(self):
        self.id += 1
        return self.id
