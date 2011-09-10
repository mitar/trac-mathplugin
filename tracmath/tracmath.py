""" TracMath - A trac plugin that renders latex formulas within a wiki page.

This has currently been tested only on trac 0.10.4 and 0.11.
"""

import codecs
import re
from cStringIO import StringIO
import os
import os.path

from trac.core import Component, implements
from trac.wiki.api import IWikiMacroProvider
from trac.wiki.api import IWikiSyntaxProvider
from trac.mimeview.api import IHTMLPreviewRenderer
from trac.web import IRequestHandler
from trac.util import escape
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

rePNG = re.compile(r'.+png$')
reGARBAGE = [
             re.compile(r'.+aux$'),
             re.compile(r'.+log$'),
             re.compile(r'.+tex$'),
             re.compile(r'.+dvi$'),
            ]
reLABEL = re.compile(r'\\label\{(.*?)\}')

class TracMathPlugin(Component):
    implements(IWikiMacroProvider, IHTMLPreviewRenderer, IRequestHandler, IWikiSyntaxProvider)

    def __init__(self):
        self._load_config()

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
        from hashlib import sha1
        from subprocess import Popen, PIPE
        import shlex

        if not name == 'latex':
            return 'Unknown macro %s' % (name)

        label = None
        for line in content.split("\n"):
            m = reLABEL.match(content)
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
                return self.show_err("Problem creating tex file: %s" % (e))

            os.chdir(self.cache_dir)
            cmd = "%s -interaction nonstopmode %s" % (self.latex_cmd, texname)
            self.log.debug("Running latex command: " + cmd)
            latex_proc = Popen(shlex.split(cmd), stdout=PIPE, stderr=PIPE)
            (out, err) = latex_proc.communicate()

            if len(err) and len(out):
                return self.show_err('Unable to call: %s %s %s' % (cmd, out, err))

            cmd = "".join([self.dvipng_cmd,
                    " -T tight -x %s -z 9 -bg Transparent " % self.mag_factor,
                    "-o %s %s" % (imgname, key + '.dvi')])
            self.log.debug("Running dvipng command: " + cmd)
            dvipng_proc = Popen(shlex.split(cmd), stdout=PIPE, stderr=PIPE)
            (out, err) = dvipng_proc.communicate()

            if len(err) and len(out):
                pass # TODO: check for real errors

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
                if ext.match(name):
                    os.unlink(os.path.join(self.cache_dir, name))
            if name.endswith('.png'):
                png_files.append(name)

        if len(png_files) > self.max_png:
            stats = sorted((os.stat(os.path.join(self.cache_dir, name)).st_mtime, name) 
                           for name in png_files)
            # We don't delete the last max_png elements, so remove them from the list
            del stats[-self.max_png:]
            for stat in stats:
                os.unlink(os.path.join(self.cache_dir, stat[1]))

    def _load_config(self):
        """Load the tracmath trac.ini configuration."""

        # defaults
        tmp = '/tmp/tracmath'
        latex = '/usr/bin/latex'
        dvipng = '/usr/bin/dvipng'
        max_png = 500
        mag_factor = 1200

        if 'tracmath' not in self.config.sections():
            self.log.warn("The [tracmath] section is not configured in trac.ini. Using defaults.")

        self.cache_dir = self.config.get('tracmath', 'cache_dir') or tmp
        self.latex_cmd = self.config.get('tracmath', 'latex_cmd') or latex
        self.dvipng_cmd = self.config.get('tracmath', 'dvipng_cmd') or dvipng
        self.max_png = self.config.get('tracmath', 'max_png') or max_png
        self.max_png = int(self.max_png)
        self.use_dollars = self.config.get('tracmath', 'use_dollars') or "False"
        self.use_dollars = self.use_dollars.lower() in ("true", "on", "enabled")
        self.mag_factor = self.config.get('tracmath', 'mag_factor') or mag_factor

        if not os.path.exists(self.latex_cmd):
            self.log.error('Could not find latex binary at ' + self.latex_cmd)
        if not os.path.exists(self.dvipng_cmd):
            self.log.error('Could not find dvipng binary at ' + self.dvipng_cmd)
        if not os.path.exists(self.cache_dir):
            os.mkdir(self.cache_dir, 0777)

        #TODO: check correct values.
        return ''

    def _show_err(self, msg):
        """Display msg in an error box, using Trac style."""
        buf = StringIO()
        buf.write('<div id="content" class="error"><div class="message"> \n\
                   <strong>TracMath macro processor has detected an \n\
                   error. Please fix the problem before continuing. \n\
                   </strong> <pre>%s</pre> \n\
                   </div></div>' % escape(msg))
        self.log.error(msg)
        return buf
