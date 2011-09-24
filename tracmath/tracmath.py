""" TracMath - A trac plugin that renders latex formulas within a wiki page.
"""

import codecs
import re
import os
import os.path
from hashlib import sha1
import subprocess

from genshi.builder import tag

from trac.config import BoolOption, IntOption, Option
from trac.core import Component, implements
from trac.wiki.api import IWikiMacroProvider
from trac.wiki.api import IWikiSyntaxProvider
from trac.mimeview.api import IHTMLPreviewRenderer
from trac.web import IRequestHandler
from trac.util import escape
from trac.util.text import to_unicode
from trac.util.translation import _
from trac import mimeview

__author__ = 'Reza Lotun'
__author_email__ = 'rlotun@gmail.com'

tex_preamble = r"""
\documentclass{article}
\usepackage{amsmath}
\usepackage{amsthm}
\usepackage{amssymb}
\usepackage{bm}
\pagestyle{empty}
\begin{document}
"""

rePNG = re.compile(r'\.png$')
reGARBAGE = [
             re.compile(r'\.aux$'),
             re.compile(r'\.log$'),
             re.compile(r'\.tex$'),
             re.compile(r'\.dvi$'),
            ]
reLABEL = re.compile(r'\\label\{(.*?)\}')

class TracMathPlugin(Component):
    implements(IWikiMacroProvider, IHTMLPreviewRenderer, IRequestHandler, IWikiSyntaxProvider)

    cache_dir_option = Option("tracmath", "cache_dir", "tmcache",
            """The directory that will be used to cache the generated images.
            If not given as an absolute path, the path will be relative to
            the Trac environment's directory.
            """)
    
    max_png = IntOption("tracmath", "max_png", 500,
            """The maximum number of files that the cache should
            contain.""")
    
    mag_factor = IntOption("tracmath", "mag_factor", 1100,
            """dvipng magnification factor.""")
    
    compression = IntOption("tracmath", "compression", 6,
            """PNG compression level.""")
    
    latex_cmd = Option("tracmath", "latex_cmd", "/usr/bin/latex",
            """Full path to the latex program (including the filename).""")
    
    dvipng_cmd = Option("tracmath", "dvipng_cmd", "/usr/bin/dvipng",
            """Full path to the dvipng program (including the filename).""")
    
    use_dollars = BoolOption("tracmath", "use_dollars", False,
            """Should support for dollar wiki syntax be enabled.""")

    # IWikiSyntaxProvider methods
    #   stolen from http://trac-hacks.org/ticket/248

    def get_wiki_syntax(self):
        if self.use_dollars:
            yield (r"\$\$(?P<displaymath>.*?)\$\$", self._format_math_block)
            yield (r"\$(?P<latex>.*?)\$", self._format_math_inline)

    def _format_math_block(self, formatter, ns, match):
        return "<blockquote>" + self.expand_macro(formatter, 'latex', ns) + "</blockquote>"

    def _format_math_inline(self, formatter, ns, match):
        return self.expand_macro(formatter, 'latex', ns)

    def get_link_resolvers(self):
        return []

    # IWikiMacroProvider methods
    def get_macros(self):
        yield 'latex'

    def get_macro_description(self, name):
        if name == 'latex':
            return """
            This plugin allows embedded equations in Trac markup.
            Basically a port of http://www.amk.ca/python/code/mt-math to Trac.

            Simply use
            {{{
              {{{
              #!latex
              [latex code]
              }}}
            }}}
            for a block of LaTeX code.

            If `use_dollars` is enabled in `trac.ini`, then you can also use
            `$[latex formula]$` for inline math or `$$[latex formula]$$` for
            display math.
            """
    def expand_macro(self, formatter, name, content):
        errmsg = self._load_config()
        if errmsg:
            return self._show_err(errmsg)

        return self._internal_render(formatter.req, name, content)

    # IHTMLPreviewRenderer methods
    def get_quality_ratio(self, mimetype):
        if mimetype in ('application/tracmath'):
            return 2
        return 0

    def render(self, req, mimetype, content, filename=None, url=None):
        text = hasattr(content, 'read') and content.read() or content
        return self._internal_render(req, 'latex', text)

    # IRequestHandler methods
    def match_request(self, req):
        return req.path_info.startswith('/tracmath')

    def process_request(self, req):
        errmsg = self._load_config()
        if errmsg:
            return self._show_err(errmsg)

        pieces = [item for item in req.path_info.split('/tracmath') if item]

        if pieces:
            pieces = [item for item in pieces[0].split('/') if item]
            if pieces:
                name = pieces[0]
                img_path = os.path.join(self.cache_dir, name)
                return req.send_file(img_path,
                        mimeview.get_mimetype(img_path))
        return

    # Internal implementation
    def _internal_render(self, req, name, content):
        if not name == 'latex':
            return 'Unknown macro %s' % (name)

        label = None
        for line in content.split("\n"):
            m = reLABEL.search(content)
            if m:
                label = m.group(1)

        key = sha1(content.encode('utf-8')).hexdigest()

        imgname = key + '.png'
        imgpath = os.path.join(self.cache_dir, imgname)

        if not os.path.exists(imgpath):
            texname = key + '.tex'
            texpath = os.path.join(self.cache_dir, texname)

            try:
                f = codecs.open(texpath, encoding='utf-8', mode='w')
                f.write(tex_preamble)
                f.write(content)
                f.write('\end{document}')
                f.close()
            except Exception, e:
                return self._show_err("Problem creating tex file: %s" % (e))

            os.chdir(self.cache_dir)
            args = [
                self.latex_cmd,
                "-interaction=nonstopmode",
                texname,
            ]
            self.log.debug("Running command: %s", " ".join(args))
            failure, errmsg = self._launch("", *args)
            if failure:
                return self._show_err(errmsg)

            args = [
                self.dvipng_cmd,
                "-T", "tight",
                "-z", str(self.compression),
                "-x", str(self.mag_factor),
                "-bg","Transparent",
                "-o", imgname,
                "%s.dvi" % key,
            ]
            self.log.debug("Running command: %s", " ".join(args))
            failure, errmsg = self._launch("", *args)
            if failure:
                return self._show_err(errmsg)

            self._manage_cache()
        else:
            # Touch the file to keep it live in the cache
            os.utime(imgpath, None)

        result = '<img src="%s/tracmath/%s" alt="%s" />' % (req.base_url, imgname, content)
        if label:
            result = '<a name="%s">(%s)<a/>&nbsp;%s' % (label, label, result)
        return result

    def _manage_cache(self):
        png_files = []
        for name in os.listdir(self.cache_dir):
            for ext in reGARBAGE:
                if ext.search(name):
                    os.unlink(os.path.join(self.cache_dir, name))
            if rePNG.search(name):
                png_files.append(name)

        if len(png_files) > self.max_png:
            stats = sorted((os.stat(os.path.join(self.cache_dir, name)).st_mtime, name) 
                           for name in png_files)
            # We don't delete the last max_png elements, so remove them from the list
            del stats[-self.max_png:]
            for stat in stats:
                os.unlink(os.path.join(self.cache_dir, stat[1]))

    def _load_config(self):
        """Preprocess the tracmath trac.ini configuration."""

        self.cache_dir = self.cache_dir_option
        if not self.cache_dir:
            return _("The [tracmath] section is missing the cache_dir field.")

        if not os.path.isabs(self.cache_dir):
            self.cache_dir = os.path.join(self.env.path, self.cache_dir)

        if not os.path.exists(self.cache_dir):
            os.mkdir(self.cache_dir)

        if not os.path.exists(self.latex_cmd):
            return _("Could not find latex binary at %(cmd)s", cmd=self.latex_cmd)

        if not os.path.exists(self.dvipng_cmd):
            return _("Could not find dvipng binary at %(cmd)s", cmd=self.dvipng_cmd)

    def _launch(self, encoded_input, *args):
        """Launch a process (cmd), and returns exitcode, stdout + stderr"""
        # Note: subprocess.Popen doesn't support unicode options arguments
        # (http://bugs.python.org/issue1759845) so we have to encode them.
        # Anyway, dot expects utf-8 or the encoding specified with -Gcharset.
        encoded_cmd = []
        for arg in args:
            if isinstance(arg, unicode):
                arg = arg.encode('utf-8', 'replace')
            encoded_cmd.append(arg)
        p = subprocess.Popen(encoded_cmd, stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if encoded_input:
            p.stdin.write(encoded_input)
        p.stdin.close()
        out = p.stdout.read()
        err = p.stderr.read()
        failure = p.wait() != 0
        if failure or err or out:
            return (failure, tag.p(tag.br(), _("The command:"),
                         tag.pre(repr(' '.join(encoded_cmd))),
                         (_("succeeded but emitted the following output:"),
                          _("failed with the following output:"))[failure],
                         out and tag.pre(repr(out)),
                         err and tag.pre(repr(err))))
        else:
            return (False, None)

    def _show_err(self, msg):
        """Display msg in an error box, using Trac style."""
        if isinstance(msg, str):
            msg = to_unicode(msg)
        self.log.error(msg)
        if isinstance(msg, unicode):
            msg = tag.pre(escape(msg))
        return tag.div(
                tag.strong(_("TracMath macro processor has detected an error. "
                             "Please fix the problem before continuing.")),
                msg, class_="system-message")
