import urllib2
import datetime
import re
import xml.etree.ElementTree as etree
from xml.sax.saxutils import escape

TYPE_ICE = 0x100
TYPE_IC_EC = 0x80
TYPE_IR = 0x40
TYPE_REGIONAL = 0x20
TYPE_URBAN = 0x10
TYPE_BUS = 0x08
TYPE_BOAT = 0x04
TYPE_SUBWAY = 0x02
TYPE_TRAM = 0x01
TYPE_ALL = 0x1ff
TYPE_TRAINS = TYPE_ICE | TYPE_IC_EC | TYPE_IR | TYPE_REGIONAL | TYPE_URBAN


def send_xml_request(body):
	request = urllib2.Request(
		'http://reiseauskunft.bahn.de/bin/mgate.exe',
		body,
		{'Accept': 'text/xml'}
	)
	response = urllib2.urlopen(request)
	return etree.parse(response)


def transport_type_as_string(transport_type):
	return ('000000000' + (bin(transport_type)[2:]))[-9:]


# Parses a timestamp of the format "01d10:05:00" into a tuple of (day offset, time)
def parse_daystamp(time_string):
	return (
		int(time_string[0:2]),
		datetime.datetime.strptime(time_string[3:], '%H:%M:%S').time()
	)


class Movement(object):
	def __repr__(self):
		time = self.time.strftime("%H:%M:%S")
		if self.day_add == 1:
			time += " + 1 day"
		elif self.day_add > 1:
			time += " + %d days" % self.day_add

		return time


class Arrival(Movement):
	def __init__(self, day_add, time, platform, can_exit):
		self.day_add = day_add
		self.time = time
		self.platform = platform
		self.can_exit = can_exit

	@staticmethod
	def from_xml(arrival_xml):

		(day_add, time) = parse_daystamp(arrival_xml.find('./Time').text)
		platform = arrival_xml.find('./Platform/Text').text
		can_exit = arrival_xml.get('getOut') == 'YES'

		return Arrival(day_add, time, platform, can_exit)


class Departure(Movement):
	def __init__(self, day_add, time, platform, can_enter):
		self.day_add = day_add
		self.time = time
		self.platform = platform
		self.can_enter = can_enter

	@staticmethod
	def from_xml(departure_xml):

		(day_add, time) = parse_daystamp(departure_xml.find('./Time').text)
		platform = departure_xml.find('./Platform/Text').text
		can_enter = departure_xml.get('getIn') == 'YES'

		return Departure(day_add, time, platform, can_enter)


class Stop(object):
	def __init__(self, station, arrival, departure, is_normal):
		self.station = station
		self.arrival = arrival
		self.departure = departure
		self.is_normal = is_normal

	def __repr__(self):
		times = []
		if self.arrival:
			times.append('arr %s' % repr(self.arrival))
		if self.departure:
			times.append('dep %s' % repr(self.departure))
		return "<%s: %s>" % (self.station.name.encode('utf-8'), ', '.join(times))

	@staticmethod
	def from_xml(basicstop_xml):

		station = Station.from_xml(basicstop_xml.find('Station'))

		arrival_xml = basicstop_xml.find('Arr')
		if arrival_xml is None:
			arrival = None
		else:
			arrival = Arrival.from_xml(arrival_xml)

		departure_xml = basicstop_xml.find('Dep')
		if departure_xml is None:
			departure = None
		else:
			departure = Departure.from_xml(departure_xml)

		is_normal = basicstop_xml.get('type') == 'NORMAL'

		return Stop(station, arrival, departure, is_normal)


class Service(object):
	def __init__(self, journey, validity):
		self.journey = journey
		self.validity = validity

	@staticmethod
	def from_xml(journeyres_xml, timetable):
		journey = Journey.from_xml(journeyres_xml.find('./Journey'))
		validity = Validity.from_xml(journeyres_xml.find('./ServiceDays'), timetable)

		return Service(journey, validity)


class Journey(object):
	def __init__(self, ref, stops, attributes):
		self.ref = ref
		self.stops = stops
		self.attributes = attributes
		self.name = attributes.get('NAME')
		self.category = attributes.get('CATEGORY')
		self.number = attributes.get('NUMBER')

	@staticmethod
	def from_xml(journey_xml):
		ref = JourneyRef.from_xml(journey_xml.find('./JHandle'))

		stops = [
			Stop.from_xml(basicstop_xml)
			for basicstop_xml in journey_xml.findall('./PassList/BasicStop')
		]

		attributes = {}
		for attribute_xml in journey_xml.findall('./JourneyAttributeList/JourneyAttribute'):
			attr_name = (
				attribute_xml.find('./Attribute').get('type')
				or attribute_xml.find('./Attribute').get('code')
			)
			attr_value = attribute_xml.find('./Attribute/AttributeVariant/Text')
			attributes[attr_name] = attr_value.text if (attr_value is not None) else None

		return Journey(ref, stops, attributes)


class JourneyRef(object):
	def __init__(self, cycle, puic, tnr):
		self.cycle = cycle
		self.puic = puic
		self.tnr = tnr

	def as_xml(self):
		return "<JHandle tNr='%s' puic='%s' cycle='%s'/>" % (escape(self.tnr), escape(self.puic), escape(self.cycle))

	def get_service(self):
		request_body = """<?xml version='1.0' encoding='iso-8859-1'?>
			<ReqC ver='1.1' prod='JP' lang='en' clientVersion='3.2'>
				<JourneyReq>
					%s
				</JourneyReq>
			</ReqC>
		""" % self.as_xml()
		root = send_xml_request(request_body)
		#print etree.tostring(root.getroot())
		timetable = Timetable.from_xml(root.getroot())

		return Service.from_xml(root.find('./JourneyRes'), timetable)

	@staticmethod
	def from_xml(jhandle_xml):
		return JourneyRef(
			cycle=jhandle_xml.get('cycle'),
			puic=jhandle_xml.get('puic'),
			tnr=jhandle_xml.get('tNr')
		)


class Station(object):
	def __init__(self, id, name, lng, lat):
		self.id = id
		self.name = name
		self.lng = lng
		self.lat = lat

	def get_timetable(self, board_type, transport_type=TYPE_TRAINS, time=None, start_date=None, end_date=None):
		if not time:
			time = datetime.datetime.now().time()

		if not start_date:
			start_date = datetime.date.today()

		if not end_date:
			end_date = datetime.date.today()

		request_body = """<?xml version='1.0' encoding='iso-8859-1'?>
			<ReqC ver='1.1' prod='JP' lang='en' clientVersion='3.2'>
				<STBReq boardType='DEP' detailLevel='2'>
					<Time>%s</Time>
					<Period>
						<DateBegin>%s</DateBegin><DateEnd>%s</DateEnd>
					</Period>
					<TableStation externalId='%s'/>
					<ProductFilter>%s</ProductFilter>
				</STBReq>
			</ReqC>
		""" % (
			time.strftime('%H:%M:%S'),
			start_date.strftime('%Y%m%d'),
			end_date.strftime('%Y%m%d'),
			escape(self.id),
			transport_type_as_string(transport_type),
		)

		root = send_xml_request(request_body)
		# print etree.tostring(root.getroot())

		entries_xml = root.findall('./STBResIPhone/Entries/StationBoardEntry')

		return [
			{
				'approxDelay': entry_xml.get('approxDelay'),
				'category': entry_xml.get('category'),
				'direction': entry_xml.get('direction'),
				'name': entry_xml.get('name'),
				'product': entry_xml.get('product'),
				'scheduledDate': entry_xml.get('scheduledDate'),
				'scheduledPlatform': entry_xml.get('scheduledPlatform'),
				'scheduledTime': entry_xml.get('scheduledTime'),
				'station': Station.from_xml(entry_xml.find('Station')),
				'journeyRef': JourneyRef.from_xml(entry_xml.find('JHandle')),
			}
			for entry_xml in entries_xml
		]

	def get_departure_timetable(self, **kwargs):
		return self.get_timetable('DEP', **kwargs)

	def get_arrival_timetable(self, **kwargs):
		return self.get_timetable('ARR', **kwargs)

	def __str__(self):
		return "<Station: %s>" % self.name.encode('utf-8')

	def __repr__(self):
		return "<Station: %s>" % self.name.encode('utf-8')

	def as_xml(self):
		return "<Station name='%s' externalId='%s' type='WGS84' x='%s' y='%s'/>" % (
			escape(self.name),
			escape(self.id),
			int(self.lng * 1000000),
			int(self.lat * 1000000),
		)

	@staticmethod
	def from_xml(station_xml):
		return Station(
			id=station_xml.get('externalId'),
			name=station_xml.get('name'),
			lng=int(station_xml.get('x')) / 1000000.0,
			lat=int(station_xml.get('y')) / 1000000.0
		)

	@staticmethod
	def search(query, count=20):

		request_body = """<?xml version='1.0' encoding='iso-8859-1'?>
			<ReqC ver='1.1' prod='JP' lang='en' clientVersion='3.2'>
				<LocValReq id='L' maxNr='%s' sMode='1'>
					<ReqLoc type='ST' match='%s'/>
				</LocValReq>
			</ReqC>
		""" % (int(count), escape(query))

		root = send_xml_request(request_body)
		# print etree.tostring(root.getroot())
		stations_xml = root.findall('./LocValRes/Station')

		return [Station.from_xml(xml) for xml in stations_xml]

	@staticmethod
	def near(lat, lng, count=20):
		request_body = """<?xml version='1.0' encoding='iso-8859-1'?>
			<ReqC ver='1.1' prod='JP' lang='en' clientVersion='3.2'>
				<LocValReq id='L' maxNr='%s' sMode='1'>
					<Coord type='ALLTYPE' x='%s' y='%s'/>
				</LocValReq>
			</ReqC>
		""" % (int(count), int(lng * 1000000), int(lat * 1000000))

		root = send_xml_request(request_body)
		# print etree.tostring(root.getroot())
		stations_xml = root.findall('./LocValRes/Station')

		return [Station.from_xml(xml) for xml in stations_xml]


class ConnectionSection(object):
	def __init__(self, departure, journey, arrival):
		self.departure = departure
		self.journey = journey
		self.arrival = arrival

	@staticmethod
	def from_xml(consection_xml):
		departure = Stop.from_xml(consection_xml.find('./Departure/BasicStop'))
		journey = Journey.from_xml(consection_xml.find('./Journey'))
		arrival = Stop.from_xml(consection_xml.find('./Arrival/BasicStop'))

		return ConnectionSection(departure, journey, arrival)


class Timetable(object):
	def __init__(self, start_date, end_date):
		self.start_date = start_date
		self.end_date = end_date
		# print start_date, end_date

	@staticmethod
	def from_xml(response_xml):
		start_date = datetime.datetime.strptime(response_xml.get('timeTableBegin'), '%Y%m%d').date()
		end_date = datetime.datetime.strptime(response_xml.get('timeTableEnd'), '%Y%m%d').date()

		return Timetable(start_date, end_date)


class Validity(object):
	def __init__(self, service_bits, timetable):
		self.service_bits = service_bits
		self.timetable = timetable

	def get_bitstring(self):
		bits = [
			# convert hex digit to binary, strip off the '0b' prefix, pad to 4 chars
			('0000' + (bin(int(digit, 16))[2:]))[-4:]
			for digit in self.service_bits
		]
		return ''.join(bits)

	def slice(self, start_date, end_date):
		bitstring = self.get_bitstring()

		if start_date < self.timetable.start_date or start_date > self.timetable.end_date:
			raise IndexError('Start date out of range')
		if end_date < self.timetable.start_date or end_date > self.timetable.end_date:
			raise IndexError('End date out of range')
		if end_date < start_date:
			raise IndexError('End date cannot be before start date')

		start_offset = (start_date - self.timetable.start_date).days
		end_offset = (end_date - self.timetable.start_date).days

		return bitstring[start_offset:(end_offset + 1)]

	def get_days(self):
		days = []
		one_day = datetime.timedelta(days=1)
		date = self.timetable.start_date
		for bit in self.get_bitstring():
			if bit == '1':
				days.append(date)
			date += one_day

		return days

	@staticmethod
	def from_xml(service_days_xml, timetable):
		service_bits = service_days_xml.find('./ServiceBits').text

		return Validity(service_bits, timetable)


class Connection(object):

	def __init__(self, date, departure_stop, arrival_stop, transfer_count, duration, products, validity, sections):
		self.date = date
		self.departure_stop = departure_stop
		self.arrival_stop = arrival_stop
		self.transfer_count = transfer_count
		self.duration = duration
		self.products = products
		self.validity = validity
		self.sections = sections

	@staticmethod
	def from_xml(connection_xml, timetable):
		date_string = connection_xml.find('./Overview/Date').text
		date = datetime.datetime.strptime(date_string, '%Y%m%d').date()

		departure_stop = Stop.from_xml(connection_xml.find('./Overview/Departure/BasicStop'))
		arrival_stop = Stop.from_xml(connection_xml.find('./Overview/Arrival/BasicStop'))
		transfer_count = int(connection_xml.find('./Overview/Transfers').text)

		duration_string = connection_xml.find('./Overview/Duration/Time').text
		(d, h, m, s) = re.match(r'(\d\d)d(\d\d):(\d\d):(\d\d)', duration_string).groups()
		duration = datetime.timedelta(days=int(d), hours=int(h), minutes=int(m), seconds=int(s))
		products = [product.get('cat') for product in connection_xml.findall('./Overview/Products/Product')]

		validity = Validity.from_xml(connection_xml.find('./Overview/ServiceDays'), timetable)

		sections = [
			ConnectionSection.from_xml(section_xml)
			for section_xml in connection_xml.findall('./ConSectionList/ConSection')
		]

		return Connection(date, departure_stop, arrival_stop, transfer_count, duration, products, validity, sections)

	@staticmethod
	def find(origin, destination, time=None, arrival=False,
		earlier_connection_count=0, later_connection_count=3,
		transport_type=TYPE_ALL, direct=False, bike=False):

		if not time:
			time = datetime.datetime.now()

		request_body = """<?xml version='1.0' encoding='iso-8859-1'?>
			<ReqC ver='1.1' prod='JP' lang='en' clientVersion='3.2'>
				<ConReq ivCons='no' oevCons='yes' deliverPolyline='1'>
					<Start>
						%s
						<Prod prod='%s' direct='%s' bike='%s'/>
					</Start>
					<Dest>
						%s
					</Dest>
					<ReqT date='%s' time='%s' a='%s'/>
					<RFlags b='%d' f='%d' sMode='N'/>
					<GISParameters>
						<Front>
							<IndividualTransport type='FOOT' minDist='0' maxDist='3000' speed='100'/>
						</Front>
						<Back>
							<IndividualTransport type='FOOT' minDist='0' maxDist='3000' speed='100'/>
						</Back>
						<Total>
							<IndividualTransport type='FOOT' minDist='0' maxDist='3000' speed='100'/>
						</Total>
					</GISParameters>
				</ConReq>
			</ReqC>
		""" % (
			origin.as_xml(),
			transport_type_as_string(transport_type),
			'1' if direct else '0',
			'1' if bike else '0',
			destination.as_xml(),
			time.strftime('%Y%m%d'),
			time.strftime('%H:%M:%S'),
			'1' if arrival else '0',
			earlier_connection_count,
			later_connection_count,
		)

		root = send_xml_request(request_body)
		#print etree.tostring(root.getroot())
		timetable = Timetable.from_xml(root.getroot())

		return [
			Connection.from_xml(connection_xml, timetable)
			for connection_xml in root.findall('./ConRes/ConnectionList/Connection')
		]
