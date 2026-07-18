import { Module, NestModule, MiddlewareConsumer, RequestMethod } from '@nestjs/common';
import { ProxyController } from './proxy.controller';
import { AuthMiddleware } from './auth.middleware';

@Module({
  imports: [],
  controllers: [ProxyController],
  providers: [],
})
export class AppModule implements NestModule {
  configure(consumer: MiddlewareConsumer) {
    consumer
      .apply(AuthMiddleware)
      .forRoutes({ path: '*', method: RequestMethod.ALL });
  }
}
