import psycopg2
import psycopg2.sql
import os
import json
import parse
import requests


def handler(context, event):
    request_body = json.loads(event.body)
    test_id = request_body.get('test_case')
    test_result = request_body.get('test_result')

    # get a connection to the postgresSql database
    conn = connect_to_db()

    # cur is the cursor of current connection
    cur = conn.cursor()

    # update test case result
    cur.execute('update test_cases set result=%s where oid=%s', (test_result, test_id))

    # get running node = current node
    context.logger.info(f'select running_node from test_cases where oid = {test_id}')

    cur.execute('select running_node from test_cases where oid = %s', (test_id,))
    current_node = cur.fetchall()[0]

    cur.execute('select job from test_cases where oid = %s', (test_id,))
    current_job = cur.fetchall()[0]

    if test_result != 'failed':

        # release the node
        call_function('release_node', json.dumps({
            'node_id': current_node,
        }))

    # lock job for writes so we don’t clash with another test running on another node, since
    # we are updating the job’s properties
    # test_case.job.lock()

    # get job properties
    jobs_properties = get_jobs_properties_for_reporting(cur, current_job)

    # if the test failed, set the job as failed
    if test_result == 'failed':

        cur.execute('update test_cases set result=%s where oid=%s', (test_result, test_id))

        # gets jobs properties, then with them report job result with slack_notifier and github_status_updater
        report_job_result(cur, jobs_properties, 'pending')

        return

    # if the test succeeded, check if we should run another test on this node
    # or release it into the wild
    else:

        # get the next test without a result and without a node
        next_pending_test_id = get_next_test_id(cur)

        # if there’s no test, we were the last test to succeed
        if next_pending_test_id is None:

            # report the job success
            report_job_result(cur, jobs_properties, 'success')

            # release the node
            call_function('release_node', json.dumps({
                'node_id': current_node,
            }))

        else:

            # set the test case running on that node in the db
            cur.execute('update nodes set current_test_case = %s where oid=%s returning oid', (next_pending_test_id, current_node))

            running_node = cur.fetchall()[0]
            cur.execute('update test_cases set running_node = %s where oid=%s', (running_node, next_pending_test_id))

            # run the specific test case on the specific node. since this is the first time this node will
            # run a test, pull is required
            context.logger.info_with('started test case', Node=current_node, Test_case_id=next_pending_test_id)

            call_function('run_test_case', json.dumps({'node': current_node,
                                                       'pull_required': False,
                                                       'test_case_id': next_pending_test_id}))


def get_jobs_properties_for_reporting(cur, current_job):
    cur.execute('select (github_username, github_url, commit_sha) from jobs where oid = %s', (current_job,))
    jobs_properties = cur.fetchall()
    return jobs_properties[0]


def get_next_test_id(cur):
    cur.execute('select oid from test_cases where running_node is null and result is null')
    returned_value = cur.fetchone()

    # if command returned not-null, return first value of returned tuple,
    return returned_value if returned_value is None else returned_value[0]


def report_job_result(cur, github_username, git_url, commit_sha, result):

    # get slack username
    slack_username = convert_slack_username(cur, github_username)

    # notify via slack that build is running
    call_function('slack_notifier', json.dumps({
        'slack_username': slack_username,
        'message': 'Your Nuci test started'
    }))

    # notify via github that build is running
    call_function('github_status_updater', json.dumps({

        'state': result,
        'repo_url': git_url,
        'commit_sha': commit_sha
    }))


def release_noes(node):
    pass


# # returns map with {
# # artifact-tests-url: value,
# # state: state_value,
# # github_url: github_url_value,
# # commit_sha: commit_sha_value,
# # github_username: github_username_value}
# def get_job_properties(cur, test_id):
#     cur.execute('select job from test_cases where oid=%s', (test_id, ))
#     job_properties = cur.fetchone()
#
#     if job_properties is None:
#         return None;
#
#     keys = cur.execute(f'\d test_cases;')
#     map = {}
#
#     for key_index, key in enumerate(keys):
#         map[key] = job_properties[key_index]
#
#     return map


# get slack username from db according to given github_username
def convert_slack_username(db_cursor, github_username):

    # convert github username to slack username
    db_cursor.execute('select slack_username from users where github_username=%s', (github_username, ))

    # get slack username of given github username
    slack_username = db_cursor.fetchone()

    if slack_username is None:
        raise ValueError('Failed converting git username to slack username')

    # get first value of the postgresSQL tuple answer
    return slack_username[0]



# gets current cursor and command, alerts if returned 0 values
def get_cursors_one_result(cur, cmd):
    returned_value = cur.fetchone()

    if returned_value is None:
        return Exception(cmd)

    return returned_value[0]


# connect to db, return psycopg2 connection
def connect_to_db():

    # get postgres connection information from container's environment vars
    postgres_info = parse_env_var_info(os.environ.get('PGINFO'))

    # raise NameError if env var not found, or found in wrong format
    if postgres_info is None:
        raise ValueError('Local variable PGINFO in proper format (user:password@host:port or user:password@host)'
                         ' could not be found')

    postgres_user, postgres_password, postgres_host, postgres_port = postgres_info

    # connect to postgres database, set autocommit to True to avoid committing
    conn = psycopg2.connect(host=postgres_host, user=postgres_user, password=postgres_password, port=postgres_port)
    conn.autocommit = True

    return conn


def parse_env_var_info(formatted_string):
    if formatted_string is not None:

        # check if default formatted string given
        if parse.parse('{}:{}@{}:{}', formatted_string) is not None:
            return list(parse.parse('{}:{}@{}:{}', formatted_string))

        # if not, try get same format without the port specification
        if parse.parse('{}:{}@{}', formatted_string) is not None:
            return list(parse.parse('{}:{}@{}', formatted_string)) + [5432]
    return None


# calls given function with given arguments, returns body of response
def call_function(function_name, function_arguments=None):
    functions_ports = {
        'database_init': 36543,
        'github_status_updater': 36544,
        'slack_notifier': 36545,
        'build_and_push_artifacts': 36546,
        'run_test_case': 36547,
        'release_node': 36550
    }

    # if given_host is specified post it instead of
    given_host = os.environ.get('DOCKER_HOST', '172.17.0.1')
    response = requests.post(f'http://{given_host}:{functions_ports[function_name]}',
                             data=function_arguments)

    return response.text

