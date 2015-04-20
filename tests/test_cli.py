import datetime
import os
from click.testing import CliRunner
import collections
from mock import MagicMock, Mock
import yaml
from senza.cli import cli
import boto.exception
from senza.traffic import PERCENT_RESOLUTION, StackVersion


def test_invalid_definition():
    data = {}

    runner = CliRunner()

    with runner.isolated_filesystem():
        with open('myapp.yaml', 'w') as fd:
            yaml.dump(data, fd)

        result = runner.invoke(cli, ['print', 'myapp.yaml', '--region=myregion', '123'], catch_exceptions=False)

    assert 'Error: Invalid value for "definition"' in result.output


def test_version():
    runner = CliRunner()
    result = runner.invoke(cli, ['--version'])
    assert result.output.startswith('Senza ')


def test_print_basic(monkeypatch):
    monkeypatch.setattr('boto.cloudformation.connect_to_region', lambda x: MagicMock())

    data = {'SenzaInfo': {'StackName': 'test'}, 'SenzaComponents': [{'Configuration': {'Type': 'Senza::Configuration',
                                                                                       'ServerSubnets': {
                                                                                           'eu-west-1': [
                                                                                               'subnet-123']}}},
                                                                    {'AppServer': {
                                                                        'Type': 'Senza::TaupageAutoScalingGroup',
                                                                        'InstanceType': 't2.micro',
                                                                        'Image': 'AppImage',
                                                                        'TaupageConfig': {'runtime': 'Docker',
                                                                                          'source': 'foo/bar'}}}]}

    runner = CliRunner()

    with runner.isolated_filesystem():
        with open('myapp.yaml', 'w') as fd:
            yaml.dump(data, fd)

        result = runner.invoke(cli, ['print', 'myapp.yaml', '--region=myregion', '123', '1.0-SNAPSHOT'],
                               catch_exceptions=False)

    assert 'AWSTemplateFormatVersion' in result.output
    assert 'subnet-123' in result.output


def test_print_auto(monkeypatch):
    images = [MagicMock(name='Taupage-AMI-123', id='ami-123')]

    zone = MagicMock()
    zone.name = 'zo.ne'
    cert = {'server_certificate_name': 'zo-ne', 'arn': 'arn:aws:123'}
    cert_response = {
        'list_server_certificates_response': {'list_server_certificates_result': {'server_certificate_metadata_list': [
            cert
        ]}}}

    sg = MagicMock()
    sg.name = 'app-sg'
    sg.id = 'sg-007'

    monkeypatch.setattr('boto.cloudformation.connect_to_region', lambda x: MagicMock())
    monkeypatch.setattr('boto.vpc.connect_to_region', lambda x: MagicMock())
    monkeypatch.setattr('boto.iam.connect_to_region', lambda x: MagicMock(list_server_certs=lambda: cert_response))
    monkeypatch.setattr('boto.route53.connect_to_region', lambda x: MagicMock(get_zones=lambda: [zone]))
    monkeypatch.setattr('boto.ec2.connect_to_region', lambda x: MagicMock(get_all_images=lambda filters: images,
                                                                          get_all_security_groups=lambda: [sg]))

    sns = MagicMock()
    topic = {'TopicArn': 'arn:123:mytopic'}
    sns.get_all_topics.return_value = {'ListTopicsResponse': {'ListTopicsResult': {'Topics': [topic]}}}
    monkeypatch.setattr('boto.sns.connect_to_region', MagicMock(return_value=sns))

    data = {'SenzaInfo': {'StackName': 'test',
                          'OperatorTopicId': 'mytopic',
                          'Parameters': [{'ImageVersion': {'Description': ''}}]},
            'SenzaComponents': [{'Configuration': {'Type': 'Senza::StupsAutoConfiguration'}},
                                {'AppServer': {'Type': 'Senza::TaupageAutoScalingGroup',
                                               'InstanceType': 't2.micro',
                                               'TaupageConfig': {'runtime': 'Docker',
                                                                 'source': 'foo/bar:{{Arguments.ImageVersion}}'},
                                               'IamRoles': ['app-myrole'],
                                               'SecurityGroups': ['app-sg', 'sg-123']}},
                                {'AppLoadBalancer': {'Type': 'Senza::WeightedDnsElasticLoadBalancer',
                                                     'HTTPPort': 8080,
                                                     'SecurityGroups': ['app-sg']}}]}

    runner = CliRunner()

    with runner.isolated_filesystem():
        with open('myapp.yaml', 'w') as fd:
            yaml.dump(data, fd)

        result = runner.invoke(cli, ['print', 'myapp.yaml', '--region=myregion', '123', '1.0-SNAPSHOT'],
                               catch_exceptions=False)

    assert 'AWSTemplateFormatVersion' in result.output
    assert 'subnet-123' in result.output
    assert 'source: foo/bar:1.0-SNAPSHOT' in result.output


def test_init(monkeypatch):
    monkeypatch.setattr('boto.ec2.connect_to_region', lambda x: MagicMock())
    monkeypatch.setattr('boto.cloudformation.connect_to_region', lambda x: MagicMock())
    monkeypatch.setattr('boto.vpc.connect_to_region', lambda x: MagicMock())
    monkeypatch.setattr('boto.iam.connect_to_region', lambda x: MagicMock())

    runner = CliRunner()

    with runner.isolated_filesystem():
        result = runner.invoke(cli, ['init', 'myapp.yaml', '--region=myregion', '-v', 'test=123'],
                               catch_exceptions=False, input='1\nsdf\nsdf\n8080\n/\n')
        assert os.path.exists('myapp.yaml')
        with open('myapp.yaml') as fd:
            generated_definition = yaml.safe_load(fd)

    assert 'Generating Senza definition file myapp.yaml.. OK' in result.output
    assert generated_definition['SenzaInfo']['StackName'] == 'sdf'


def test_instances(monkeypatch):
    stack = MagicMock()
    inst = MagicMock()
    monkeypatch.setattr('boto.ec2.connect_to_region', lambda x: MagicMock(get_only_instances=lambda filters: [inst]))
    monkeypatch.setattr('boto.cloudformation.connect_to_region', lambda x: MagicMock(describe_stacks=lambda x: [stack]))

    runner = CliRunner()

    data = {'SenzaInfo': {'StackName': 'test'}}

    with runner.isolated_filesystem():
        with open('myapp.yaml', 'w') as fd:
            yaml.dump(data, fd)
        result = runner.invoke(cli, ['instances', 'myapp.yaml', '--region=myregion', '1'],
                               catch_exceptions=False)

    assert 'Launched' in result.output


def test_resources(monkeypatch):
    stack = MagicMock(stack_name='test-1', creation_time=datetime.datetime.now())
    res = MagicMock(timestamp=datetime.datetime.now(), logical_resource_id='MyTestResource', resource_type='AWS::abc')
    monkeypatch.setattr('boto.cloudformation.connect_to_region',
                        lambda x: MagicMock(describe_stack_resources=lambda x: [res],
                                            list_stacks=lambda stack_status_filters: [stack]))

    runner = CliRunner()

    data = {'SenzaInfo': {'StackName': 'test'}}

    with runner.isolated_filesystem():
        with open('myapp.yaml', 'w') as fd:
            yaml.dump(data, fd)
        result = runner.invoke(cli, ['resources', 'myapp.yaml', '--region=myregion', '1'],
                               catch_exceptions=False)

    assert 'MyTestResource' in result.output


def test_events(monkeypatch):
    stack = MagicMock(stack_name='test-1', creation_time=datetime.datetime.now())
    evt = MagicMock(timestamp=datetime.datetime.now(), logical_resource_id='MyTestEventRes', resource_type='foobar')
    monkeypatch.setattr('boto.cloudformation.connect_to_region',
                        lambda x: MagicMock(describe_stack_events=lambda x: [evt],
                                            list_stacks=lambda stack_status_filters: [stack]))

    runner = CliRunner()

    data = {'SenzaInfo': {'StackName': 'test'}}

    with runner.isolated_filesystem():
        with open('myapp.yaml', 'w') as fd:
            yaml.dump(data, fd)
        result = runner.invoke(cli, ['events', 'myapp.yaml', '--region=myregion', '1'],
                               catch_exceptions=False)

    assert 'MyTestEventRes' in result.output


def test_list(monkeypatch):
    stack = MagicMock(stack_name='test-stack-1', creation_time=datetime.datetime.now())
    monkeypatch.setattr('boto.cloudformation.connect_to_region',
                        lambda x: MagicMock(list_stacks=lambda stack_status_filters: [stack]))

    runner = CliRunner()

    data = {'SenzaInfo': {'StackName': 'test-stack'}}

    with runner.isolated_filesystem():
        with open('myapp.yaml', 'w') as fd:
            yaml.dump(data, fd)
        result = runner.invoke(cli, ['list', 'myapp.yaml', '--region=myregion'],
                               catch_exceptions=False)

    assert 'test-stack' in result.output


def test_delete(monkeypatch):
    cf = MagicMock()
    stack = MagicMock(stack_name='test-1')
    cf.list_stacks.return_value = [stack]
    monkeypatch.setattr('boto.cloudformation.connect_to_region', lambda x: cf)

    runner = CliRunner()

    data = {'SenzaInfo': {'StackName': 'test'}}

    with runner.isolated_filesystem():
        with open('myapp.yaml', 'w') as fd:
            yaml.dump(data, fd)
        result = runner.invoke(cli, ['delete', 'myapp.yaml', '--region=myregion', '1'],
                               catch_exceptions=False)

    assert 'OK' in result.output


def test_create(monkeypatch):
    cf = MagicMock()
    sns = MagicMock()
    topic = MagicMock()
    sns.get_all_topics.return_value = {'ListTopicsResponse': {'ListTopicsResult': {'Topics': [topic]}}}
    monkeypatch.setattr('boto.cloudformation.connect_to_region', MagicMock(return_value=cf))
    monkeypatch.setattr('boto.sns.connect_to_region', MagicMock(return_value=sns))

    runner = CliRunner()

    data = {'SenzaInfo': {
        'OperatorTopicId': 'my-topic',
        'StackName': 'test', 'Parameters': [{'MyParam': {'Type': 'String'}}]},
            'SenzaComponents': [{'Config': {'Type': 'Senza::Configuration'}}]}

    with runner.isolated_filesystem():
        with open('myapp.yaml', 'w') as fd:
            yaml.dump(data, fd)

        result = runner.invoke(cli, ['create', 'myapp.yaml', '--dry-run', '--region=myregion', '1', 'my-param-value'],
                               catch_exceptions=False)
        assert 'DRY-RUN' in result.output

        result = runner.invoke(cli, ['create', 'myapp.yaml', '--region=myregion', '1', 'my-param-value'],
                               catch_exceptions=False)
        assert 'OK' in result.output

        cf.create_stack.side_effect = boto.exception.BotoServerError('sdf', 'already exists',
                                                                     {'Error': {'Code': 'AlreadyExistsException'}})
        result = runner.invoke(cli, ['create', 'myapp.yaml', '--region=myregion', '1', 'my-param-value'],
                               catch_exceptions=True)
        assert 'Stack test-1 already exists' in result.output


def test_traffic(monkeypatch):

    r53conn = Mock(name='r53conn')

    monkeypatch.setattr('boto.ec2.connect_to_region', MagicMock())
    monkeypatch.setattr('boto.ec2.elb.connect_to_region', MagicMock())
    monkeypatch.setattr('boto.cloudformation.connect_to_region', MagicMock())
    monkeypatch.setattr('boto.route53.connect_to_region', r53conn)
    stacks = [
        StackVersion('myapp', 'v1', 'myapp.example.org', 'some-lb'),
        StackVersion('myapp', 'v2', 'myapp.example.org', 'another-elb'),
        StackVersion('myapp', 'v3', 'myapp.example.org', 'elb-3'),
        StackVersion('myapp', 'v4', 'myapp.example.org', 'elb-4'),
    ]
    monkeypatch.setattr('senza.traffic.get_stack_versions', MagicMock(return_value=stacks))

    # start creating mocking of the route53 record sets and Application Versions
    # this is a lot of dirty and nasty code. Please, somebody help this code.

    def record(dns_identifier, weight):
        rec = MagicMock(name=dns_identifier + '-record',
                        weight=weight,
                        identifier=dns_identifier,
                        type='CNAME')
        rec.name = 'myapp.example.org.'
        return rec

    rr = MagicMock()
    records = collections.OrderedDict()

    for ver, percentage in [('v1', 60),
                        ('v2', 30),
                        ('v3', 10),
                        ('v4', 0)]:
        dns_identifier = 'myapp-{}'.format(ver)
        records[dns_identifier] = record(dns_identifier, percentage * PERCENT_RESOLUTION)

    rr.__iter__ = lambda x: iter(records.values())

    def add_change(op, dns_name, rtype, ttl, identifier, weight):
        print('CHANGE', op, weight)
        if op == 'CREATE':
            x = MagicMock(weight=weight, identifier=identifier)
            x.name = "myapp.example.org."
            x.type = "CNAME"
            records[identifier] = x
        return MagicMock(name='change')

    def add_change_record(op, record):
        print('CHANGE', op, record.identifier, record.weight)
        if op == 'DELETE':
            records[record.identifier].weight = 0
        elif op == 'UPSERT':
            records[record.identifier].weight = record.weight

    rr.add_change = add_change
    rr.add_change_record = add_change_record

    r53conn().get_zone().get_records.return_value = rr

    runner = CliRunner()

    common_opts = ['traffic', '--region=my-region', 'myapp']

    def run(opts):
        result = runner.invoke(cli, common_opts + opts, catch_exceptions=False)
        print(result.output)
        return result

    def weights():
        return [r.weight for r in records.values()]

    with runner.isolated_filesystem():
        run(['v4', '100'])
        assert weights() == [0, 0, 0, 200]

        run(['v3', '10'])
        assert weights() == [0, 0, 20, 180]

        run(['v2', '0.5'])
        assert weights() == [0, 1, 20, 179]

        run(['v1', '1'])
        assert weights() == [2, 1, 19, 178]

        run(['v4', '95'])
        assert weights() == [1, 1, 13, 185]

        run(['v4', '100'])
        assert weights() == [0, 0, 0, 200]

        run(['v4', '10'])
        assert weights() == [0, 0, 0, 200]

        run(['v4', '0'])
        assert weights() == [0, 0, 0, 0]
