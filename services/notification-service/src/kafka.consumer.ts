import { Injectable, OnModuleInit, OnModuleDestroy } from '@nestjs/common';
import { Kafka, Consumer } from 'kafkajs';
import { NotificationGateway } from './notification.gateway';

@Injectable()
export class KafkaConsumerService implements OnModuleInit, OnModuleDestroy {
  private kafka: Kafka;
  private consumer: Consumer;

  constructor(private readonly gateway: NotificationGateway) {
    const bootstrapServers = process.env.KAFKA_BOOTSTRAP_SERVERS || 'kafka:9092';
    this.kafka = new Kafka({
      clientId: 'notification-service',
      brokers: [bootstrapServers],
    });
    this.consumer = this.kafka.consumer({ groupId: 'notification-service-group' });
  }

  async onModuleInit() {
    await this.consumer.connect();
    await this.consumer.subscribe({ topic: 'dashboard.update', fromBeginning: false });
    
    console.log('Subscribed to Kafka topic: dashboard.update');
    
    await this.consumer.run({
      eachMessage: async ({ message }) => {
        try {
          const payload = JSON.parse(message.value.toString());
          const tenantId = payload.tenant_id;
          const updateType = payload.update_type;
          const data = payload.data;
          
          if (tenantId && updateType) {
            // Send WebSocket notification to the tenant room
            this.gateway.sendToTenant(tenantId, 'dashboard_update', {
              update_type: updateType,
              data: data,
              timestamp: payload.timestamp || new Date().toISOString(),
            });
          }
        } catch (error) {
          console.error('Error processing Kafka message in notifications:', error);
        }
      },
    });
  }

  async onModuleDestroy() {
    await this.consumer.disconnect();
  }
}
