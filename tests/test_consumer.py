import asyncio

import aioamqp
import pytest
from aioamqp.channel import Channel

from aioamqp_consumer import Consumer, Process, Queue
from aioamqp_consumer.base_middlewares import Middleware
from aioamqp_consumer.consumer import _ConsumerCloseException
from tests.utils import Arg, future


pytestmark = pytest.mark.asyncio


class TestConsumer:

    async def test_start__expected_right_reconnect_timeouts_and_calls(self, mocker, event_loop):
        # arrange
        consumer = Consumer(
            middleware=Process(lambda _: future(None)),
            queue=mocker.sentinel.queue,
            connection_params=[mocker.sentinel.connection_params],
            prefetch_count=1,
            default_reconnect_timeout=3.0,
            max_reconnect_timeout=5.0,
        )
        mocker.patch.object(consumer, '_connect', side_effect=[
            future(),
            future(exception=aioamqp.AioamqpException()),
            future(),
        ])
        mocker.patch.object(consumer, '_disconnect', return_value=future())
        mocker.patch.object(consumer, '_process_queue', return_value=future())
        gather = mocker.patch('aioamqp_consumer.consumer.gather', side_effect=[
            future(exception=aioamqp.AioamqpException()),
            future(exception=_ConsumerCloseException()),
        ])
        mocker.patch.object(asyncio, 'sleep', return_value=future())

        # act
        await consumer.start(event_loop)

        # assert
        connection_closed_future_arg = Arg()
        assert consumer._connect.call_count == 3
        consumer._connect.assert_called_with(
            connection_closed_future=connection_closed_future_arg,
            loop=event_loop,
        )
        assert isinstance(connection_closed_future_arg.value, asyncio.Future)

        consumer._disconnect.assert_called_once_with()

        assert consumer._process_queue.call_count == 2
        consumer._process_queue.assert_called_with(loop=event_loop)

        assert gather.call_count == 2
        closed_future_arg = Arg()
        gather.assert_called_with(
            connection_closed_future_arg.value,
            closed_future_arg,
            consumer._process_queue.return_value,
            loop=event_loop,
        )
        assert isinstance(closed_future_arg.value, asyncio.Future)
        assert asyncio.sleep.call_args_list == [
            mocker.call(3.0, loop=event_loop),
            mocker.call(5.0, loop=event_loop),
        ]

    async def test_close(self, mocker):
        # arrange
        consumer = Consumer(
            middleware=Process(lambda _: future(None)),
            queue=mocker.sentinel.queue,
            connection_params=[mocker.sentinel.connection_params],
            prefetch_count=1,
            default_reconnect_timeout=3.0,
            max_reconnect_timeout=5.0,
        )
        consumer._closed_future = asyncio.Future()
        consumer._closed_ok = asyncio.Event()
        consumer._closed_ok.set()
        mocker.spy(consumer._closed_ok, 'wait')

        # act
        await consumer.close()

        # assert
        assert consumer._closed_future.done()
        assert isinstance(consumer._closed_future.exception(), _ConsumerCloseException)
        consumer._closed_ok.wait.assert_called_once_with()

    async def test__connect(self, mocker, event_loop):
        # arrange
        consumer = Consumer(
            middleware=Process(lambda _: future(None)),
            queue=mocker.sentinel.queue,
            connection_params=[mocker.sentinel.connection_params],
            prefetch_count=mocker.sentinel.prefetch_count,
            default_reconnect_timeout=3.0,
            max_reconnect_timeout=5.0,
        )

        channel = mocker.Mock(spec=Channel)
        channel.basic_qos.return_value = future()

        connect_and_open_channel = mocker.patch(
            'aioamqp_consumer.consumer.connect_and_open_channel',
            return_value=future((
                mocker.sentinel.transport,
                mocker.sentinel.protocol,
                channel
            )),
        )

        declare_queue = mocker.patch('aioamqp_consumer.consumer.declare_queue', return_value=future())

        connection_closed_future = asyncio.Future()

        # act
        await consumer._connect(
            connection_closed_future=connection_closed_future,
            loop=event_loop,
        )

        # assert
        on_error_arg = Arg()
        connect_and_open_channel.assert_called_once_with(
            connection_params=mocker.sentinel.connection_params,
            on_error=on_error_arg,
            loop=event_loop,
        )

        exc = Exception()
        await on_error_arg.value(exc)
        await on_error_arg.value(exc)
        assert connection_closed_future.done()
        assert connection_closed_future.exception() is exc

        channel.basic_qos.assert_called_once_with(prefetch_count=mocker.sentinel.prefetch_count)

        declare_queue.assert_called_once_with(channel=channel, queue=mocker.sentinel.queue)

    async def test__disconnect(self, mocker):
        # arrange
        consumer = Consumer(
            middleware=Process(lambda _: future(None)),
            queue=mocker.sentinel.queue,
            connection_params=[mocker.sentinel.connection_params],
            prefetch_count=mocker.sentinel.prefetch_count,
            default_reconnect_timeout=3.0,
            max_reconnect_timeout=5.0,
        )

        channel = mocker.Mock(spec=Channel)
        channel.is_open = True
        channel.close.return_value = future()
        consumer._channel = channel

        protocol = mocker.Mock(spec=Channel)
        protocol.state = aioamqp.protocol.OPEN
        protocol.close.return_value = future()
        consumer._protocol = protocol

        transport = mocker.Mock(spec=Channel)
        consumer._transport = transport

        # act
        await consumer._disconnect()

        # assert
        assert consumer._channel is None
        channel.close.assert_called_once_with()

        assert consumer._protocol is None
        protocol.close.assert_called_once_with()

        assert consumer._transport is None
        transport.close.assert_called_once_with()

    async def test__process_queue(self, mocker, event_loop):
        # arrange
        consumer = Consumer(
            middleware=Process(lambda _: future(None)),
            queue=Queue('queue_name'),
            connection_params=[mocker.sentinel.connection_params],
            prefetch_count=mocker.sentinel.prefetch_count,
            default_reconnect_timeout=3.0,
            max_reconnect_timeout=5.0,
            tag='tag',
        )

        consumer._middleware = mocker.Mock(spec=Middleware)
        consumer._middleware.run.return_value = future()

        consumer._channel = mocker.Mock(spec=Channel)
        consumer._channel.basic_consume.return_value = future()

        Message = mocker.patch('aioamqp_consumer.consumer.Message')

        # act
        await consumer._process_queue(loop=event_loop)

        # assert
        callback_arg = Arg()
        consumer._channel.basic_consume.assert_called_once_with(
            callback=callback_arg,
            queue_name='queue_name',
            consumer_tag='tag',
        )

        input_queue_arg = Arg()
        output_queue_arg = Arg()
        consumer._middleware.run.assert_called_once_with(
            input_queue=input_queue_arg,
            output_queue=output_queue_arg,
            loop=event_loop,
        )

        assert output_queue_arg.value.maxsize == 1

        await callback_arg.value(
            channel=mocker.sentinel.channel,
            body=mocker.sentinel.body,
            envelope=mocker.sentinel.envelope,
            properties=mocker.sentinel.properties,
        )
        message = input_queue_arg.value.get_nowait()
        assert message is Message.return_value
        Message.assert_called_once_with(
            channel=mocker.sentinel.channel,
            body=mocker.sentinel.body,
            envelope=mocker.sentinel.envelope,
            properties=mocker.sentinel.properties,
        )
