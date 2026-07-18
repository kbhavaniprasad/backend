import { Controller, All, Req, Res, HttpStatus } from '@nestjs/common';
import { Request, Response } from 'express';
import * as proxy from 'express-http-proxy';

// Service URLs configured from environment variables
const SERVICE_URLS = {
  auth: process.env.AUTH_SERVICE_URL || 'http://auth-service:8001',
  lead: process.env.LEAD_SERVICE_URL || 'http://lead-service:8002',
  agent_a: process.env.AGENT_A_SERVICE_URL || 'http://agent-a-service:8003',
  agent_b: process.env.AGENT_B_SERVICE_URL || 'http://agent-b-service:8004',
  voice: process.env.VOICE_SERVICE_URL || 'http://voice-service:8005',
  calendar: process.env.CALENDAR_SERVICE_URL || 'http://calendar-service:8006',
  analytics: process.env.ANALYTICS_SERVICE_URL || 'http://analytics-service:8007',
};

@Controller()
export class ProxyController {

  @All('api/v1/auth*')
  proxyAuth(@Req() req: Request, @Res() res: Response) {
    return this.doProxy(req, res, SERVICE_URLS.auth);
  }

  @All('api/v1/leads*')
  proxyLeads(@Req() req: Request, @Res() res: Response) {
    return this.doProxy(req, res, SERVICE_URLS.lead);
  }

  @All('api/v1/webhooks*')
  proxyWebhooks(@Req() req: Request, @Res() res: Response) {
    return this.doProxy(req, res, SERVICE_URLS.lead);
  }

  @All('api/v1/conversations*')
  proxyConversations(@Req() req: Request, @Res() res: Response) {
    return this.doProxy(req, res, SERVICE_URLS.agent_a);
  }

  @All('api/v1/agent*')
  proxyAgentA(@Req() req: Request, @Res() res: Response) {
    return this.doProxy(req, res, SERVICE_URLS.agent_a);
  }

  @All('api/v1/evaluations*')
  proxyEvaluations(@Req() req: Request, @Res() res: Response) {
    return this.doProxy(req, res, SERVICE_URLS.agent_b);
  }

  @All('api/v1/learnings*')
  proxyLearnings(@Req() req: Request, @Res() res: Response) {
    return this.doProxy(req, res, SERVICE_URLS.agent_b);
  }

  @All('api/v1/reports*')
  proxyReports(@Req() req: Request, @Res() res: Response) {
    return this.doProxy(req, res, SERVICE_URLS.agent_b);
  }

  @All('api/v1/calls*')
  proxyCalls(@Req() req: Request, @Res() res: Response) {
    return this.doProxy(req, res, SERVICE_URLS.voice);
  }

  @All('api/v1/calendar*')
  proxyCalendar(@Req() req: Request, @Res() res: Response) {
    return this.doProxy(req, res, SERVICE_URLS.calendar);
  }

  @All('api/v1/analytics*')
  proxyAnalytics(@Req() req: Request, @Res() res: Response) {
    return this.doProxy(req, res, SERVICE_URLS.analytics);
  }

  @All('health')
  getHealth(@Req() req: Request, @Res() res: Response) {
    return res.status(HttpStatus.OK).json({
      status: 'ok',
      service: 'api-gateway',
      timestamp: new Date().toISOString(),
    });
  }

  private doProxy(req: Request, res: Response, targetUrl: string) {
    const customReq = req as any;
    
    return proxy(targetUrl, {
      proxyReqOptDecorator: (proxyReqOpts, srcReq) => {
        // If JWT token was authenticated successfully, inject user/tenant headers
        if (customReq.user) {
          proxyReqOpts.headers['X-User-Id'] = customReq.user.id;
          proxyReqOpts.headers['X-Tenant-Id'] = customReq.user.tenant_id;
          proxyReqOpts.headers['X-User-Role'] = customReq.user.role;
          proxyReqOpts.headers['X-User-Email'] = customReq.user.email;
        }
        return proxyReqOpts;
      },
      proxyReqPathResolver: (srcReq) => {
        // Keep original path and query parameters intact
        return srcReq.originalUrl;
      },
      userResDecorator: (proxyRes, proxyResData, userReq, userRes) => {
        return proxyResData;
      },
    })(req, res, (err) => {
      console.error(`Proxy Error requesting ${targetUrl}:`, err);
      res.status(HttpStatus.BAD_GATEWAY).json({
        message: 'Bad Gateway. Downstream microservice is unreachable.',
        error: err.message,
      });
    });
  }
}
