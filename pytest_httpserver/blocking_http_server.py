from queue import Empty
from queue import Queue
from typing import Any
from typing import Dict
from typing import Mapping
from typing import Optional
from typing import Pattern
from typing import Union

from werkzeug.wrappers import Request
from werkzeug.wrappers import Response

from pytest_httpserver.httpserver import METHOD_ALL
from pytest_httpserver.httpserver import UNDEFINED
from pytest_httpserver.httpserver import HeaderValueMatcher
from pytest_httpserver.httpserver import HttpServerBase
from pytest_httpserver.httpserver import QueryMatcher
from pytest_httpserver.httpserver import RequestHandlerBase
from pytest_httpserver.httpserver import URIPattern


class BlockingRequestHandler(RequestHandlerBase):
    """
    Provides responding to a request synchronously.

    This class should only be instantiated inside the implementation of the :py:class:`BlockingHttpServer`.
    """

    def __init__(self):
        self.response_queue = Queue()

    def respond_with_response(self, response: Response):
        self.response_queue.put_nowait(response)


class BlockingHttpServer(HttpServerBase):
    """
    Server instance which enables synchronous matching for incoming requests.

    :param timeout: waiting time in seconds for matching and responding to an incoming request.
        manager

    For further parameters and attributes see :py:class:`HttpServerBase`.
    """

    def __init__(self, timeout: int = 30, **kwargs):
        super().__init__(**kwargs)
        self.timeout = timeout
        self.request_queue: Queue[Request] = Queue()
        self.request_handlers: Dict[Request, Queue[BlockingRequestHandler]] = {}

    def assert_request(
        self,
        uri: Union[str, URIPattern, Pattern[str]],
        method: str = METHOD_ALL,
        data: Union[str, bytes, None] = None,
        data_encoding: str = "utf-8",
        headers: Optional[Mapping[str, str]] = None,
        query_string: Union[None, QueryMatcher, str, bytes, Mapping] = None,
        header_value_matcher: Optional[HeaderValueMatcher] = None,
        json: Any = UNDEFINED,
        timeout: int = 30,
    ) -> BlockingRequestHandler:
        """
        Wait for an incoming request and check whether it matches according to the given parameters.

        If the incoming request matches, a request handler is created and registered,
        otherwise assertion error is raised.
        The request handler can be used once to respond for the request.
        If no response is performed in the period given in the timeout parameter of the constructor
        or no request arrives in the `timeout` period, assertion error is raised.

        :param uri: URI of the request. This must be an absolute path starting with ``/``, a
            :py:class:`URIPattern` object, or a regular expression compiled by :py:func:`re.compile`.
        :param method: HTTP method of the request. If not specified (or `METHOD_ALL`
            specified), all HTTP requests will match.
        :param data: payload of the HTTP request. This could be a string (utf-8 encoded
            by default, see `data_encoding`) or a bytes object.
        :param data_encoding: the encoding used for data parameter if data is a string.
        :param headers: dictionary of the headers of the request to be matched
        :param query_string: the http query string, after ``?``, such as ``username=user``.
            If string is specified it will be encoded to bytes with the encode method of
            the string. If dict is specified, it will be matched to the ``key=value`` pairs
            specified in the request. If multiple values specified for a given key, the first
            value will be used. If multiple values needed to be handled, use ``MultiDict``
            object from werkzeug.
        :param header_value_matcher: :py:class:`HeaderValueMatcher` that matches values of headers.
        :param json: a python object (eg. a dict) whose value will be compared to the request body after it
            is loaded as json. If load fails, this matcher will be failed also. *Content-Type* is not checked.
            If that's desired, add it to the headers parameter.
        :param timeout: waiting time in seconds for an incoming request.

        :return: Created and registered :py:class:`BlockingRequestHandler`.

        Parameters `json` and `data` are mutually exclusive.
        """

        matcher = self.create_matcher(
            uri,
            method=method.upper(),
            data=data,
            data_encoding=data_encoding,
            headers=headers,
            query_string=query_string,
            header_value_matcher=header_value_matcher,
            json=json,
        )

        try:
            request = self.request_queue.get(timeout=timeout)
        except Empty:
            raise AssertionError(f"Waiting for request {matcher} timed out")

        diff = matcher.difference(request)

        request_handler = BlockingRequestHandler()

        self.request_handlers[request].put_nowait(request_handler)

        if diff:
            request_handler.respond_with_response(self.respond_nohandler(request))
            raise AssertionError(f"Request {matcher} does not match: {diff}")

        return request_handler

    def dispatch(self, request: Request) -> Response:
        """
        Dispatch a request for synchronous matching.

        This method queues the request for matching and waits for the request handler.
        If there was no request handler, error is responded,
        otherwise it waits for the response of request handler.
        If no response arrives, assertion error is raised, otherwise the response is returned.

        :param request: the request object from the werkzeug library.
        :return: the response object what the handler responded, or a response which contains the error.
        """

        self.request_handlers[request] = Queue()
        try:
            self.request_queue.put_nowait(request)

            try:
                request_handler = self.request_handlers[request].get(timeout=self.timeout)
            except Empty:
                return self.respond_nohandler(request)

            try:
                return request_handler.response_queue.get(timeout=self.timeout)
            except Empty:
                assertion = AssertionError(f"No response for request: {request}")
                self.add_assertion(assertion)
                raise assertion
        finally:
            del self.request_handlers[request]
