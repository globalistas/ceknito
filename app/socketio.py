from flask_socketio import SocketIO, join_room
from flask_login import current_user
from flask_jwt_extended import decode_token
from flask import request
from .models import rconn
from . import misc
import gevent
from gevent import monkey
import json
import random
import re
from wheezy.html.utils import escape_html
import logging
import time
from engineio.payload import Payload
from functools import wraps

# Increase max decode packets to avoid dropped messages
Payload.max_decode_packets = 100

# Time in seconds for purging items from the Redis set used to decide
# which instance should do the socketio.emit for messages received via
# Redis subscription.
NAME_KEY_KEEPALIVE = 3  # Increased from 1 for better stability

# Redis keys and patterns
INSTANCES_KEY = "throat-socketio-instances"
CHAT_HISTORY_KEY = "chathistory"
PUBSUB_PATTERN = "/send:*"
MESSAGE_PATTERN = r"/send:(.+?):(.+)"
CHAT_HISTORY_LIMIT = 50  # Increased from 20 to store more chat history

# Message size limits
MAX_MESSAGE_LENGTH = 250


def log_event(func):
    """Decorator to log socket.io events"""

    @wraps(func)
    def wrapper(*args, **kwargs):
        logger = logging.getLogger("socketio.events")
        event_name = func.__name__
        data = args[1] if len(args) > 1 else ""
        logger.debug(f"EVENT {event_name} {data}")
        return func(*args, **kwargs)

    return wrapper


class SocketIOWithLogging(SocketIO):
    def init_app(self, app, **kwargs):
        super().init_app(app, **kwargs)
        self.__logger = logging.getLogger(app.logger.name + ".socketio")

        # Only start background processes if gevent monkey patching is active
        if monkey.is_module_patched("os"):
            # Generate a unique instance name (shorter is fine)
            self.instance_name = "".join(
                chr(random.randrange(97, 123)) for _ in range(6)
            )

            # Start background tasks
            gevent.spawn(self._refresh_name_key)
            gevent.spawn(self._emit_messages)

    def emit(self, event, *args, **kwargs):
        """Emit an event, publishing to Redis if room is specified"""
        self.__logger.debug("EMIT %s %s %s", event, args[0], kwargs)

        # Publish to Redis if we have a room
        if "room" in kwargs:
            namespace = kwargs.get("namespace", "/")
            channel = f"{namespace}:{event}:{kwargs['room']}"
            try:
                rconn.publish(channel, json.dumps(args[0]))
            except Exception as e:
                self.__logger.error(f"Redis publish error: {e}")

        # Call original emit method
        super().emit(event, *args, **kwargs)

    def on(self, message, namespace=None):
        """Decorator for Socket.IO event handlers with logging"""

        def decorator(handler):
            @wraps(handler)
            def func(*args):
                # Log the received message
                msg_data = args[0] if args else ""
                self.__logger.debug("RECV %s %s", message, msg_data)

                # Call the original handler
                return handler(*args)

            # Register with Socket.IO
            return super(SocketIOWithLogging, self).on(message, namespace)(func)

        return decorator

    def _refresh_name_key(self):
        """Keep a key set to expire in Redis (background task)"""
        while True:
            try:
                now = time.time()
                # Add this instance to the sorted set
                rconn.zadd(name=INSTANCES_KEY, mapping={self.instance_name: now})

                # Remove expired instances
                rconn.zremrangebyscore(
                    name=INSTANCES_KEY, min=0, max=now - NAME_KEY_KEEPALIVE
                )

                # Sleep for slightly less than the expiry time
                gevent.sleep(NAME_KEY_KEEPALIVE * 0.8)
            except Exception as e:
                self.__logger.error(f"Error in refresh_name_key: {e}")
                gevent.sleep(1)  # Sleep a bit on error

    def _is_emitting_instance(self):
        """Return True if this is the first instance alphabetically"""
        try:
            keys = rconn.zrange(name=INSTANCES_KEY, start=0, end=-1)
            if not keys:
                return True

            names = sorted([k.decode("utf-8") for k in keys])
            return names[0] == self.instance_name
        except Exception as e:
            self.__logger.error(f"Error checking emitting instance: {e}")
            return False

    def _emit_messages(self):
        """Listen for Redis messages and emit them as Socket.IO events (background task)"""
        try:
            pubsub = rconn.pubsub(ignore_subscribe_messages=True)
            pubsub.psubscribe(PUBSUB_PATTERN)

            for message in pubsub.listen():
                # Only process if we're the designated instance
                if not self._is_emitting_instance():
                    continue

                self.__logger.debug("PSUB %s", message)

                if message["type"] == "pmessage":
                    channel = message["channel"].decode("utf-8")
                    match = re.match(MESSAGE_PATTERN, channel)

                    if match:
                        event, room = match.groups()
                        try:
                            data = json.loads(message["data"])
                            self.emit(event, data, room=room, namespace="/snt")
                        except json.JSONDecodeError:
                            self.__logger.error(
                                "Failed to decode message on channel %s: %s",
                                channel,
                                message["data"],
                            )
        except Exception as e:
            self.__logger.error(f"Error in emit_messages: {e}")
            gevent.sleep(5)  # Sleep and retry on error
            self._emit_messages()  # Restart the subscription


# Create a single global instance
socketio = SocketIOWithLogging()


@socketio.on("msg", namespace="/snt")
def chat_message(g):
    """Handle a chat message"""
    if g.get("msg") and current_user.is_authenticated:
        message = {
            "time": time.time(),
            "user": current_user.name,
            "msg": escape_html(g.get("msg")[:MAX_MESSAGE_LENGTH]),
        }

        # Use a pipeline for multiple Redis operations
        pipeline = rconn.pipeline()
        pipeline.lpush(CHAT_HISTORY_KEY, json.dumps(message))
        pipeline.ltrim(CHAT_HISTORY_KEY, 0, CHAT_HISTORY_LIMIT)
        pipeline.execute()

        # Emit message to the chat room
        socketio.emit("msg", message, namespace="/snt", room="chat")


@socketio.on("connect", namespace="/snt")
def handle_message():
    """Handle a new connection"""
    if current_user.get_id():
        user_room = "user" + current_user.uid
        join_room(user_room)

        # Send user information
        socketio.emit(
            "uinfo",
            {
                "taken": current_user.score,
                "ntf": current_user.notifications,
                "mod_ntf": current_user.mod_notifications(),
            },
            namespace="/snt",
            room=user_room,
        )


@socketio.on("getchatbacklog", namespace="/snt")
def get_chat_backlog():
    """Send chat history to the requesting client"""
    msgs = rconn.lrange(CHAT_HISTORY_KEY, 0, CHAT_HISTORY_LIMIT)

    # Send messages in chronological order (oldest first)
    for m in msgs[::-1]:
        try:
            message = json.loads(m.decode())
            socketio.emit("msg", message, namespace="/snt", room=request.sid)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            logging.error(f"Error decoding message: {e}")


@socketio.on("deferred", namespace="/snt")
def handle_deferred(data):
    """Subscribe for notification of when work associated with a target token is done"""
    target = data.get("target")
    if target:
        target = str(target)
        join_room(target)

        # Check if result is already available
        result = rconn.get(target)
        if result is not None:
            try:
                result = json.loads(result)
                socketio.emit(
                    result["event"], result["value"], namespace="/snt", room=target
                )
            except json.JSONDecodeError as e:
                logging.error(f"Error decoding deferred result: {e}")


def send_deferred_event(event, token, data, expiration=30):
    """Send an event and store it in Redis for late subscribers"""
    event_data = json.dumps({"event": event, "value": data})

    # Use a pipeline for atomicity
    pipeline = rconn.pipeline()
    pipeline.setex(name=token, time=expiration, value=event_data)
    pipeline.execute()

    # Emit the event
    socketio.emit(event, data, namespace="/snt", room=token)


@socketio.on("subscribe", namespace="/snt")
def handle_subscription(data):
    """Handle room subscription requests"""
    sub = data.get("target")
    if not sub:
        return

    # Only join non-user rooms this way (security check)
    if not str(sub).startswith("user"):
        join_room(sub)


@socketio.on("token-login", namespace="/snt")
def token_login(data):
    """Handle JWT token-based login"""
    try:
        tokendata = decode_token(data["jwt"])
        user_id = tokendata["identity"]
        user_room = "user" + user_id

        join_room(user_room)

        # Send notification count
        notification_count = misc.get_notification_count(user_id)
        socketio.emit(
            "notification",
            {"count": notification_count},
            namespace="/snt",
            room=user_room,
        )
    except Exception as e:
        logging.error(f"Token login error: {e}")
