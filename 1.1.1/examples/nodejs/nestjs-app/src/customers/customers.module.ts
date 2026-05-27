import { Module } from '@nestjs/common';
import { BigQueryProvider } from './bigquery.provider';
import { CustomersController } from './customers.controller';
import { CustomersService } from './customers.service';

@Module({
  providers: [BigQueryProvider, CustomersService],
  controllers: [CustomersController],
  exports: [CustomersService],
})
export class CustomersModule {}
