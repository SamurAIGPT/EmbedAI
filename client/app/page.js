'use client'
import { Row, Col } from 'react-bootstrap'
import ConfigSideNav from '@components/ConfigSideNav'
import MainContainer from '@components/MainContainer'
import { ToastContainer } from 'react-toastify'

export default function Home() {
  return (
    <>
      <Row className=' pe-3 vh-100 overflow-hidden  g-0'>
        <Col className='side-bar-col' lg={3} xs={3}>
          <div>
            <div className='d-flex align-items-center justify-content-center py-4'>
              <h3>PrivateGPT</h3>
            </div>
            <ConfigSideNav />
          </div>
        </Col>
        <Col lg={9} xs={9} className='main-chat-col mt-3'>
          <MainContainer />
        </Col>
        <ToastContainer />
      </Row>
    </>
  )
}
