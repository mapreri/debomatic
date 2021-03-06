# Deb-o-Matic
#
# Copyright (C) 2007-2014 Luca Falavigna
#
# Author: Luca Falavigna <dktrkranz@debian.org>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; version 3 of the License.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA 02110-1301, USA.

import os
from configparser import ConfigParser
from argparse import ArgumentParser
from logging import basicConfig as log, debug, error, getLogger, warning
from logging import ERROR, WARNING, INFO, DEBUG
from time import sleep

from .build import FullBuild
from .commands import Command
from .modules import Module
from .process import Process, ThreadPool


class Debomatic(Process):

    def __init__(self):
        self.daemonize = True
        self.setlog('%(levelname)s: %(message)s')
        self.conffile = None
        self.configvers = '013a'
        self.opts = ConfigParser()
        self.rtopts = ConfigParser()
        parser = ArgumentParser()
        parser.add_argument('-c', '--configfile', metavar='file', type=str,
                            nargs=1, help='configuration file for Deb-o-Matic')
        parser.add_argument('-n', '--no-daemon', action='store_true',
                            help='do not launch Deb-o-Matic in daemon mode')
        parser.add_argument('-q', '--quit-process', action='store_true',
                            help='terminate Deb-o-Matic processes')
        args = parser.parse_args()
        if os.getuid():
            error(_('You must run Deb-o-Matic as root'))
            exit(1)
        if args.configfile:
            self.conffile = args.configfile[0]
        if args.no_daemon:
            self.daemonize = False
        self.default_options()
        self.packagedir = self.opts.get('default', 'packagedir')
        if not os.path.isdir(self.packagedir):
            error(_('Unable to access %s directory') % self.packagedir)
            exit(1)
        self.pool = ThreadPool(self.opts.getint('default', 'maxbuilds'))
        self.commandpool = ThreadPool()
        self.logfile = self.opts.get('default', 'logfile')
        if args.quit_process:
            self.shutdown()
            exit()
        self.setlog('%(levelname)s: %(message)s',
                    self.opts.get('default', 'loglevel'))
        self.mod_sys = Module((self.opts, self.rtopts, self.conffile))
        debug(_('Startup hooks launched'))
        self.mod_sys.execute_hook('on_start', {})
        debug(_('Startup hooks finished'))
        try:
            self.startup()
        except RuntimeError:
            error(_('Another instance is running, aborting'))
            exit(1)

    def default_options(self):
        defaultoptions = ('builder', 'debootstrap', 'packagedir', 'configdir',
                          'architecture', 'maxbuilds', 'pbuilderhooks',
                          'inotify', 'sleep', 'logfile', 'loglevel')
        if not self.conffile:
            error(_('Configuration file has not been specified'))
            exit(1)
        if not os.path.exists(self.conffile):
            error(_('Configuration file %s does not exist') % self.conffile)
            exit(1)
        self.opts.read(self.conffile)
        if (not self.opts.has_option('internals', 'configversion') or
                not self.opts.get('internals', 'configversion') ==
                self.configvers):
            error(_('Configuration file is not at version %s') %
                  self.configvers)
            exit(1)
        for opt in defaultoptions:
            if (not self.opts.has_option('default', opt) or
                    not self.opts.get('default', opt)):
                error(_('Set "%(opt)s" in %(conffile)s') %
                      {'opt': opt, 'conffile': self.conffile})
                exit(1)

    def launcher(self):
        self.queue_files()
        if self.opts.getint('default', 'inotify'):
            try:
                self.launcher_inotify()
            except ImportError:
                self.launcher_timer()
        else:
            self.launcher_timer()

    def launcher_inotify(self):
        import pyinotify

        class PE(pyinotify.ProcessEvent, Debomatic):

            def __init__(self, parent):
                self.parent = parent

            def process_IN_CLOSE_WRITE(self, event):
                if (event.name.endswith('.changes') or
                        event.name.endswith('commands')):
                    self.parent.queue_files([event.name])

        wm = pyinotify.WatchManager()
        notifier = pyinotify.Notifier(wm, PE(self))
        wm.add_watch(self.packagedir, pyinotify.IN_CLOSE_WRITE)
        debug(_('Inotify loop started'))
        notifier.loop()

    def launcher_timer(self):
        debug(_('Timer loop started'))
        while True:
            sleep(self.opts.getint('default', 'sleep'))
            self.queue_files()

    def queue_files(self, filelist=None):
        if not filelist:
            try:
                filelist = os.listdir(self.packagedir)
            except OSError:
                error(_('Unable to access %s directory') % self.packagedir)
                exit(1)
        for filename in filelist:
            if filename.endswith('.changes'):
                b = FullBuild((self.opts, self.rtopts, self.conffile),
                              package=filename)
                self.pool.schedule(b.run)
                debug(_('Thread for %s scheduled') % filename)
            elif filename.endswith('.commands'):
                c = Command((self.opts, self.rtopts, self.conffile),
                            self.pool, filename)
                self.commandpool.schedule(c.process_command)
                debug(_('Thread for %s scheduled') % filename)

    def setlog(self, fmt, level='info'):
        loglevels = {'error': ERROR,
                     'warning': WARNING,
                     'info': INFO,
                     'debug': DEBUG}
        level = level.lower()
        if level not in loglevels:
            warning(_('Log level not valid, defaulting to "info"'))
            level = 'info'
        self.loglevel = loglevels[level]
        old_log = getLogger()
        if old_log.handlers:
            for handler in old_log.handlers:
                old_log.removeHandler(handler)
        log(level=self.loglevel, format=fmt)
