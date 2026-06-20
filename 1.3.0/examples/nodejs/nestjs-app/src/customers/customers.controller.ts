import { Controller, Get } from '@nestjs/common';
import { Customer, CustomersService } from './customers.service';

@Controller('customers')
export class CustomersController {
  constructor(private readonly customers: CustomersService) {}

  @Get()
  async list(): Promise<Customer[]> {
    return this.customers.list();
  }
}
