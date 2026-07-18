import { Module } from '@nestjs/common';
import { NotificationGateway } from './notification.gateway';
import { KafkaConsumerService } from './kafka.consumer';

@Module({
  imports: [],
  controllers: [],
  providers: [NotificationGateway, KafkaConsumerService],
})
export class AppModule {}
