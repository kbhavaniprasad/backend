import { Injectable, NestMiddleware, HttpStatus } from '@nestjs/common';
import { Request, Response, NextFunction } from 'express';
import * as jwt from 'jsonwebtoken';
import { createClient } from 'redis';

// Public endpoints that don't need JWT auth
const PUBLIC_PREFIXES = [
  '/api/v1/auth/login',
  '/api/v1/auth/register',
  '/api/v1/auth/refresh',
  '/api/v1/webhooks',
];

interface CustomRequest extends Request {
  user?: {
    id: string;
    tenant_id: string;
    role: string;
    email: string;
  };
}

@Injectable()
export class AuthMiddleware implements NestMiddleware {
  private redisClient;

  constructor() {
    const redisUrl = process.env.REDIS_URL || 'redis://:redis_secret@redis:6379';
    this.redisClient = createClient({ url: redisUrl });
    this.redisClient.connect().catch(err => {
      console.error('Redis connection error in API Gateway middleware:', err);
    });
  }

  async use(req: CustomRequest, res: Response, next: NextFunction) {
    const path = req.path;
    
    // 1. Skip auth check for public routes
    if (PUBLIC_PREFIXES.some(prefix => path.startsWith(prefix))) {
      return next();
    }

    // 2. Extract Auth Header
    const authHeader = req.headers.authorization;
    if (!authHeader || !authHeader.startsWith('Bearer ')) {
      return res.status(HttpStatus.UNAUTHORIZED).json({
        message: 'Missing or malformed Authorization header',
      });
    }

    const token = authHeader.split(' ')[1];
    
    try {
      // 3. Decode & Verify JWT
      const jwtSecret = process.env.JWT_SECRET || 'your_256_bit_jwt_secret_here';
      const decoded = jwt.verify(token, jwtSecret) as any;
      
      // Inject user and tenant details into request for downstream routing/tracking
      req.user = {
        id: decoded.sub,
        tenant_id: decoded.tenant_id,
        role: decoded.role,
        email: decoded.email,
      };

      // 4. Rate Limiting via Redis
      // Allow 100 requests per minute per tenant (could be higher in prod)
      const tenantId = decoded.tenant_id;
      const rateLimitKey = `rate_limit:${tenantId}:${Math.floor(Date.now() / 60000)}`;
      
      try {
        const count = await this.redisClient.incr(rateLimitKey);
        if (count === 1) {
          await this.redisClient.expire(rateLimitKey, 60);
        }
        
        if (count > 200) { // Limit to 200 reqs/min per tenant
          return res.status(HttpStatus.TOO_MANY_REQUESTS).json({
            message: 'Too many requests. Rate limit exceeded.',
          });
        }
      } catch (redisErr) {
        console.error('Redis Rate Limit increment failed:', redisErr);
        // Fallback: let request pass if Redis fails
      }

      next();
    } catch (err) {
      console.error('JWT Verification failed:', err);
      return res.status(HttpStatus.UNAUTHORIZED).json({
        message: 'Invalid or expired access token',
      });
    }
  }
}
