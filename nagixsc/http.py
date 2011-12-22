# Nag(ix)SC -- nagixsc/http.py
#
# Copyright (C) 2011 Sven Velt <sv@teamix.net>
#
# This program is free software; you can redistribute it and/or modify it
# under the terms of the GNU General Public License as published by the
# Free Software Foundation; either version 2 of the License, or (at your
# option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin St, Fifth Floor, Boston, MA  02110-1301 USA

import BaseHTTPServer
import ConfigParser
import SocketServer
import base64
import cgi
import mimetools
import os
import re
import socket
import sys

try:
	from hashlib import md5
except ImportError:
	from md5 import md5

##############################################################################

import nagixsc


def encode_multipart(xmlstr, httpuser=None, httppasswd=None):
	BOUNDARY = mimetools.choose_boundary()
	CRLF = '\r\n'
	L = []
	L.append('--' + BOUNDARY)
	L.append('Content-Disposition: form-data; name="xmlfile"; filename="xmlfile"')
	L.append('Content-Type: application/xml')
	L.append('')
	L.append(xmlstr)
	L.append('--' + BOUNDARY + '--')
	L.append('')
	body = CRLF.join(L)
	content_type = 'multipart/form-data; boundary=%s' % BOUNDARY
	headers = {'Content-Type': content_type, 'Content-Length': str(len(body))}

	if httpuser and httppasswd:
		headers['Authorization'] = 'Basic %s' % base64.b64encode(':'.join([httpuser, httppasswd]))

	return (headers, body)

##############################################################################

# See if we can fork or have to use Threads (Windows)
if 'ForkingMixIn' in SocketServer.__dict__:
	MixInClass = SocketServer.ForkingMixIn
else:
	MixInClass = SocketServer.ThreadingMixIn


##############################################################################

class NagixSC_HTTPServer(MixInClass, BaseHTTPServer.HTTPServer):
	def __init__(self, defaults):
		self.config_server = {}
		self.config_executor = {}
		self.config_acceptor = {}

		# Prepare reading config file
		self.cfgread = ConfigParser.SafeConfigParser(allow_no_value=True)
		self.cfgread.optionxform = str # We need case-sensitive options
		self.cfgread.readfp(defaults)


	def quit(self, returncode, message):
		print message
		sys.exit(returncode)


	def read_config_file(self):
		self.cfgread_list = self.cfgread.read(self.options.cfgfile)

		if self.cfgread_list == []:
			print 'Config file "%s" could not be read!' % self.options.cfgfile
			sys.exit(1)

		# Read server part of config
		self.read_config_server(self.cfgread)

		# Additional checks
		if self.options.daemon:
			if not os.access(os.path.dirname(self.config_server['pidfile']), os.W_OK):
				self.quit(2, 'Unable to write pidfile to "%s"!' % self.config_server['pidfile'])

		# Read executor part of config if not disabled
		if self.config_server['enable_executor']:
			self.read_config_executor(self.cfgread)

		# Read acceptor part of config if not disabled
		if self.config_server['enable_acceptor']:
			self.read_config_acceptor(self.cfgread)

		# Check SSL parameters and certificate
		if self.options.nossl:
			self.config_server['ssl'] = False

		if self.config_server['ssl']:
			if not os.path.exists(self.config_server['sslcert']):
				self.quit(2, 'SSL certificate file does not exist at "%s"!' % self.config_server['sslcert'])
			if not os.access(self.config_server['sslcert'], os.R_OK):
				self.quit(2, 'Could not read SSL certificate file at "%s"!' % self.config_server['sslcert'])


	def read_config_server(self, cfgread):
		config = {}

		config['ip'] = cfgread.get('server', 'ip')

		try:
			config['port'] = cfgread.getint('server', 'port')
		except ValueError:
			self.quit(2, 'Port "%s" not an integer!' % cfgread.get('server', 'port'))

		try:
			config['ssl'] = cfgread.getboolean('server', 'ssl')
		except ValueError:
			self.quit(2, 'Value for "ssl" ("%s") not boolean!' % cfgread.get('server', 'ssl'))

		if config['ssl']:
			config['sslcert'] = cfgread.get('server', 'sslcert')
			if not config['sslcert']:
				self.quit(2, 'SSL but no certificate file specified!')
		else:
			config['sslcert'] = None

		config['pidfile'] = cfgread.get('server', 'pidfile')

		try:
			config['enable_executor'] = cfgread.getboolean('server', 'enable_executor')
		except ValueError:
			self.quit(2, 'Value for "enable_executor" ("%s") not boolean!' % cfgread.get('server', 'enable_executor'))

		try:
			config['enable_acceptor'] = cfgread.getboolean('server', 'enable_acceptor')
		except ValueError:
			self.quit(2, 'Value for "enable_acceptor" ("%s") not boolean!' % cfgread.get('server', 'enable_acceptor'))

		self.config_server = config


	def read_config_executor(self, cfgread):
		config = {}

		config['conf_dir'] = cfgread.get('executor', 'conf_dir')
		if not os.path.exists(config['conf_dir']):
			self.quit(2, 'Conf directory "%s" does not exist!' % config['conf_dir'])

		try:
			config['remote_administration'] = cfgread.getboolean('executor', 'remote_administration')
		except ValueError:
			self.quit(2, 'Value for "remote_administration" ("%s") not boolean!' % cfgread.get('executor', 'remote_administration'))

		config['livestatus_socket'] = nagixsc.livestatus.prepare_socket(cfgread.get('executor', 'livestatus_socket'))

		config['users'] = {}
		for user in cfgread.options('executor/users'):
			config['users'][user] = cfgread.get('executor/users', user)

		config['admins'] = {}
		for admin in cfgread.options('executor/admins'):
			config['admins'][admin] = cfgread.get('executor/admins', admin)

		self.config_executor = config


	def read_config_acceptor(self, cfgread):
		config = {}

		config['mode'] = cfgread.get('acceptor', 'mode').lower()
		if not config['mode'] in ['checkresult', 'passive',]:
			self.quit(2, 'Unknown mode "%s" for acceptor!' % config['mode'])

		config['checkresult_dir'] = cfgread.get('acceptor', 'checkresult_dir')
		config['commandfile_path'] = cfgread.get('acceptor', 'commandfile_path')

		if config['mode'] == 'checkresult':
			if not os.access(config['checkresult_dir'], os.W_OK):
				self.quit(2, 'Checkresult directory "%s" not writable!' % config['checkresult_dir'])
		elif config['mode'] == 'passive':
			if not config['commandfile_path']:
				self.quit(2, 'Need a full path to command file/pipe!')

		try:
			config['acl'] = cfgread.getboolean('acceptor', 'acl')
		except ValueError:
			self.quit(2, 'Value for "acl" ("%s") not boolean!' % cfgread.get('acceptor', 'acl'))

		config['users'] = {}
		for user in cfgread.options('acceptor/users'):
			config['users'][user] = cfgread.get('acceptor/users', user)

		config['admins'] = {}
		for admin in cfgread.options('acceptor/admins'):
			config['admins'][admin] = cfgread.get('acceptor/admins', admin)

		config['acl_allowed_hosts_list'] = {}
		config['acl_allowed_hosts_re'] = {}
		if config['acl']:
			for user in cfgread.options('acceptor/acl_allowed_hosts_list'):
				config['acl_allowed_hosts_list'][user] = [ah.lstrip().rstrip() for ah in cfgread.get('acceptor/acl_allowed_hosts_list',user).split(',')]
			for user in cfgread.options('acceptor/acl_allowed_hosts_re'):
				try:
					config['acl_allowed_hosts_re'][user] = re.compile(cfgread.get('acceptor/acl_allowed_hosts_re',user))
				except re.error, error:
					self.quit(2, 'Could not read [acceptor/acl_allowed_hosts_re], "%s" in line "%s"' % (error, cfgread.get('acceptor/acl_allowed_hosts_re',user)))

		self.config_acceptor = config


	def socket_init(self):
		if not self.config_server:
			self.read_config_file()

		server_address = (self.config_server['ip'], self.config_server['port'])
		SocketServer.BaseServer.__init__(self, server_address, NagixSC_HTTPRequestHandler)

		if self.config_server['ssl']:
			try:
				# Python 2.6 includes SSL support
				import ssl
				self.socket = ssl.wrap_socket(socket.socket(self.address_family, self.socket_type), keyfile=self.config_server['sslcert'], certfile=self.config_server['sslcert'])

			except ImportError:

				# Try to import OpenSSL
				try:
					from OpenSSL import SSL
				except ImportError:
					print 'No Python SSL or OpenSSL wrapper/bindings found!'
					sys.exit(2)

				context = SSL.Context(SSL.SSLv23_METHOD)
				context.use_privatekey_file (self.config_server['sslcert'])
				context.use_certificate_file(self.config_server['sslcert'])
				self.socket = SSL.Connection(context, socket.socket(self.address_family, self.socket_type))

		else:
			self.socket = socket.socket(self.address_family, self.socket_type)

		self.server_bind()
		self.server_activate()


##############################################################################

class NagixSC_HTTPRequestHandler(BaseHTTPServer.BaseHTTPRequestHandler):

	def check_basic_auth(self, users={}, realm='Nag(ix)SC'):
		if not 'Authorization' in self.headers:
			return (False, None)

		try:
			authdata = base64.b64decode(self.headers['Authorization'].split(' ')[1]).split(':')
			if not users[authdata[0]] == md5(authdata[1]).hexdigest():
				return (False, None)
		except:
			return (False, None)

		return (True, authdata[0])


	def do_GET(self):
		return self.handle_request()


	def do_POST(self):
		return self.handle_request()


	def handle_request(self):
		path = self.path[1:].split('/') + ['', '', '', '',]

		if path[0].startswith('_exec'):
			self.handle_exec(path=path[1:])
		elif path[0].startswith('_accept'):
			self.handle_accept(path=path[1:])
		elif path[0].startswith('_proxy'):
			self.handle_proxy(path=path[1:])
		else:
			# Backward compatible guessing...
			return self.http_error(404, 'Not implemented yet!\n')


	def handle_exec(self, path):
		(status, httpuser) = self.check_basic_auth(self.server.config_executor['users'])
		if not status:
			return self.http_error_basic_auth()

		if len(path) < 3:
			path += ['', '', '',]
		(conffile, host, service) = path[0:3]

		if not conffile:
			return self.http_error(500, 'No config file specified!\n')

		if not re.search('^[a-zA-Z0-9-_]+$', conffile):
			return self.http_error(500, 'Config file name contains invalid characters!\n')

		# Prepare checkresult object
		self.checkresults = nagixsc.Checkresults()
		# Put necessary options to checkresults
		self.checkresults.options['hostfilter'] = host
		self.checkresults.options['servicefilter'] = service

		if conffile.startswith('_'):
			if conffile == '_livestatus':
				if self.server.config_executor['livestatus_socket']:
					# Read informations from livestatus
					(status, result) = nagixsc.livestatus.livestatus2dict(self.server.config_executor['livestatus_socket'], host, service)

					if status == True:
						self.checkresults.checks = result

				if not self.checkresults.checks:
					return self.http_error(500, result + '\n')

			elif conffile == '_admin':
				return self.handle_exec_admin()
			else:
				return self.http_error(500, 'Unknown method!\n')

		else:
			conffilepath = os.path.join(self.server.config_executor['conf_dir'], conffile + '.conf')
			config = nagixsc.read_inifile(conffilepath)
			if not config:
				return self.http_error(500, 'Could not read config file "%s"' % conffile)

			# Execute checks, build dict
			self.checkresults.conf2dict(config)

		if not self.checkresults.checks:
			return self.http_error(500, 'No check results')

		# Build XML
		self.checkresults.xml_from_dict()
		self.checkresults.xml_to_string()

		self.send_response(200)
		self.send_header('Content-Type', 'text/xml')
		self.end_headers()
		self.wfile.write(self.checkresults.xmlstring)


	def handle_accept(self, path):
		(status, httpuser) = self.check_basic_auth(self.server.config_acceptor['users'])
		if not httpuser:
			return self.http_error_basic_auth()

		try:
			(contenttype,paramdict) = cgi.parse_header(self.headers.getheader('content-type'))
		except TypeError:
			return self.http_error(500, 'Could not parse HTTP Headers!\n')

		if contenttype != 'multipart/form-data':
			return self.http_error(500, 'Please send data as "multipart/form-data"!\n')
		query = cgi.parse_multipart(self.rfile, paramdict)

		xmltext = query.get('xmlfile')
		if not xmltext:
			return self.http_error(500, 'No data in "xmlfile" variable found!\n')
		xmltext = xmltext[0]

		if len(xmltext) == 0:
			return self.http_error(500, 'No XML data received!\n')

		# Prepare checkresult object
		self.checkresults = nagixsc.Checkresults()

		# Put XML into checkresults
		(status, response) = self.checkresults.read_xml_from_string(xmltext)
		if not status:
			return self.http_error(500, response)

		# Check XML file basics
		(status, response) = self.checkresults.xml_check_version()
		if not status:
			return self.http_error(500, response)

		# Get timestamp and check it
		(status, response) = self.checkresults.xml_get_timestamp()
		if not status:
			return self.http_error(500, response)

		# Put XML to Python dict
		self.checkresults.xml_to_dict()

		# Apply ACLs
		if self.server.config_acceptor['acl']:
			(status, count_acl_failed) = self.checkresults.apply_acl(httpuser, self.server.config_acceptor['acl_allowed_hosts_list'], self.server.config_acceptor['acl_allowed_hosts_re'])
		else:
			count_acl_failed = 0

		# Output dependent on mode setting
		if self.server.config_acceptor['mode'] == 'checkresult':
			self.checkresults.options['checkresultdir'] = self.server.config_acceptor['checkresult_dir']
			datasink = 'check result dir'

			(status, count_services, count_failed, list_failed) = self.checkresults.dict2out_checkresult()

			# FIXME: Check result
			print (status, count_services, count_failed, list_failed)

		elif self.server.config_acceptor['mode'] == 'passive':
			self.checkresults.options['pipe'] = self.server.config_acceptor['commandfile_path']
			self.checkresults.options['pipe'] = '/dev/stdout'
			datasink = 'command file'

			(status, count_services, count_failed, response) = self.checkresults.dict2out_passive()
			# FIXME: Check result
			print (status, count_services, response)

		elif self.server.config_acceptor['mode'] == 'livestatus':
			return self.http_error(500, 'Mode not implemented!\n')

		# We did something...
		if count_failed == 0:
			returncode = 0
			output = 'Wrote %s check results to %s' % (count_services, datasink)
		elif count_failed < count_services:
			returncode = 1
			statusmsg = 'Wrote %s check results to %s, %s failed' % (count_services, datasink, count_failed)
		else:
			returncode = 2
			output = 'Could not write ANY out of %s check results to %s' % (count_services, datasink)

		if count_acl_failed > 0:
			returncode = max(returncode, 1)
			output += ' - %s check results failed ACL check' % count_acl_failed

		result = nagixsc.Checkresults()
		result.checks = [{'host_name':'_nagixsc', 'service_description':'_acceptor', 'returncode':returncode, 'output':output,},]
		result.xml_from_dict()
		result.xml_to_string()

		self.send_response(200)
		self.send_header('Content-Type', 'text/xml')
		self.end_headers()
		self.wfile.write(result.xmlstring)


	def handle_proxy(self, path):
			return self.http_error(404, output='Not implemented yet!\n')


	def http_error(self, code, output='Unknown Error', headers={} ):
		self.send_response(code)

		if 'Content-Type' not in headers:
			self.send_header('Content-Type', 'text/plain')
		if headers:
			for (k,v) in headers.iteritems():
				self.send_header(k, v)
		self.end_headers()

		self.wfile.write(output)
		return


	def http_error_basic_auth(self, message='Sorry! No action without login!\n', realm='Nag(ix)SC'):
			return self.http_error(401, message, {'WWW-Authenticate': 'Basic realm="%s"' % realm, })


	def setup(self):
		self.connection = self.request
		self.rfile = socket._fileobject(self.request, "rb", self.rbufsize)
		self.wfile = socket._fileobject(self.request, "wb", self.wbufsize)

	def version_string(self):
		return 'Nag(ix)SC %s HTTP Server ' % nagixsc.NAGIXSC_VERSION
