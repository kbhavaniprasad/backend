import {
  WebSocketGateway,
  WebSocketServer,
  SubscribeMessage,
  OnGatewayConnection,
  OnGatewayDisconnect,
} from '@nestjs/websockets';
import { Server, Socket } from 'socket.io';

@WebSocketGateway({
  cors: {
    origin: '*',
  },
})
export class NotificationGateway implements OnGatewayConnection, OnGatewayDisconnect {
  @WebSocketServer()
  server: Server;

  handleConnection(client: Socket) {
    const tenantId = client.handshake.query.tenant_id as string;
    if (tenantId) {
      client.join(tenantId);
      console.log(`Client ${client.id} connected and joined room: ${tenantId}`);
    } else {
      console.log(`Client ${client.id} connected without tenant_id`);
    }
  }

  handleDisconnect(client: Socket) {
    console.log(`Client ${client.id} disconnected`);
  }

  @SubscribeMessage('ping')
  handlePing(client: Socket, data: any): string {
    return 'pong';
  }

  sendToTenant(tenantId: string, eventName: string, payload: any) {
    this.server.to(tenantId).emit(eventName, payload);
    console.log(`Sent event ${eventName} to tenant room: ${tenantId}`);
  }
}
