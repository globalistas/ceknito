# Web

https://cekni.to/

A link and discussion aggregator with snek (python3)

## Some major improvements over upstream (phuks.co)

- new post sort type (Commented)
- reddit-like comments look&feel
- new comment sort type (Old)
- save comment
- expand all comments
- sub-specific delay for showing comment scores
- sub icons
- active sub highlighted in topbar
- sub name displayed in navbar
- sub navbar linking to sub home
- sub-specific search
- image and video posts expanded by default
- support for image uploads and embeds in comments
- working email notifications (see https://github.com/Phuks-co/throat/issues/525)
- disable notifications on a per-post/comment basis
- delete posts directly from Home page or Sub page
- config option to enable private subs
- config option for minimum post creation user level
- config option to enable live updates of recent activity sidebar
- config option to enable additional text in link post
- config option for user badge auto-assignment (Early Adopter and First Post)
- config option to send automated welcome message from site admin
- config option to specify announcements sub
- config option to specify Contact us link
- poll options limit increased to 12
- display of number of posts for a domain
- alphabetic ordering of default subs
- banning, quarantining, defaulting subs from admin interface
- sub subscribers list in admin interface
- improved image quality for thumbnails
- Bitchute video expando
- Recent Activity comments permalink
- fixed mp4 uploads
- webp image uploads
- improved diacritics support
- translations support for Welcome page
- all templates migrated to Wheezy
- post flair colors
- site color picker
- post body in post list view links to post
- post score upvote percentage display
- fixed auto-quote selected text when replying to comments
- date in chat timestamps
- shadowban support
- proxy support for metadata scraper
- support for locking stickied comments

## Dependencies:

 - A database server - MySQL, MariaDB and Postgres have been tested. Sqlite should work for messing locally
 - Redis
 - Python >= 3.9
 - A recent node/npm
 - libmagic
 - libpq-dev

## Setup:

We recommend using a virtualenv or Pyenv and setting it up to use Python 3.9 for best compatibility (higher versions might fail to build dependencies). See for e.g. [pyenv](https://github.com/pyenv/pyenv) to learn how to install multiple Python versions on your workstation. Refer to your virtual environment manager for documentation on selecting specific Python versions.

1. Install Python dependencies with `poetry install`
2. Install Node dependencies with `npm install`
3. Build the bundles with `npm run build`
4. Copy `example.config.yaml` to `config.yaml` and edit it
5. Set up the database by executing `poetry run ./throat.py migration apply`
6. Compile the translation files with `poetry run ./throat.py translations compile`

And you're done! You can run a test server by executing `poetry run ./throat.py`. For production instances we recommend setting up `gunicorn`

### Production deployments
Please read [doc/deploy.md](doc/deploy.md) for instructions to deploy on gunicorn or using docker.

## Develop on Docker
If you prefer to develop on docker
 - The provided Docker resources only support Postgres
 - You still must copy `example.config.yaml` to `config.yaml` and make any changes you want
 - In addition, configs are overridden by environment variables set in docker-compose.yml
   which reference the redis and postgres services created by docker-compose.

`make up` will bring the containerized site up and mount the app/html and app/template directories
inside the container for dev. It also runs the migrations on start-up. `make down` will spin down the containerized services.

To add an admin user to a running docker-compose application:
`docker exec throat_throat_1 ./throat.py admin add {{username}}`

If Wheezy templates are not automatically reloading in docker between changes, try `docker restart throat_throat_1`.

## Database Configuration

The default hot sort function is simple for speed, but it does not prioritize new posts over old ones as much as some people prefer.  If you define a function named `hot` in SQL in your database, you can use that instead of the default by setting `custom_hot_sort` to `True` in your `config.yaml`.  The function needs to take two arguments, a post's current score and the date it was posted.  To allow the database to cache the results, the function should only depend on the values of its arguments and should be marked `immutable`.

In addition to defining the function, you should also create an index on it to speed up the hot sort query.  Once that is done, custom functions will be faster than the default hot sort.  To implement Reddit's version of hot sort in Postgres, add the following SQL statements to your database using `psql`:

```sql
create or replace function hot(score integer, date double precision) returns numeric as $$
  select round(cast(log(greatest(abs($1), 1)) * sign($1) + ($2 - 1134028003) / 45000.0 as numeric), 7)
$$ language sql immutable;

create index on sub_post (hot(score, (EXTRACT(EPOCH FROM sub_post.posted))));
```

Other databases may require variations in the handling of the date. Custom hot sorts are not supported for Sqlite.

## Docker Deployments

### Gunicorn
```
CMD [ "gunicorn", \
      "-w", "4", \
      "-k", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", \
      "-b", "0.0.0.0:5000", \
      "throat:app" ]
```

## Authenticating with a Keycloak server

Optionally, user authentication can be done using a Keycloak server.
You will need to create a realm for the users on the server, as well
as Keycloak clients with appropriate permissions.  See
`doc/keycloak.org` for instructions.

## Deploying to AWS

You can check out the [CDK Definition of Infrastructure](https://gitlab.com/feminist-conspiracy/infrastructure) maintained by Ovarit

## Management commands
 - `./throat.py admin` to list, add or remove administrators.
 - `./throat.py default` to list, add or remove default subs.

## Tests

### Python tests

1. Python, redis, and libmagic are required, but node and postgres are not.

2. Install dependencies with `pip install -r requirements.txt`

3. Run the tests with `python -m pytest`

4. The tests are not affected by your configuration in `config.yaml`.
If you wish to run the tests against production database or
authentication servers (instead of the defaults, which are sqlite and
local authentication), you may put configuration settings in
`test_config.yaml` and run the tests with
`TEST_CONFIG=test_config.yaml python -m pytest`.  The tests *will
erase* the database supplied in the test configuration.  You can also
supply a logging configuration in `test_config.yaml` and then `pytest`
will show you the logs from failing tests.

Note: The tests currently work with Postgres and Sqlite.  Testing with
MySQL is not yet supported.

### Testing under Docker

You can run pytest in a Docker container via docker-compose with `make test`.

To pass arguments to pytest, invoke make like so: `make test ARGS="-x -k my_test"`

## Chat

If you have any questions, you can reach us on [Discord](https://discord.gg/8HFrGrzEx2)

---
